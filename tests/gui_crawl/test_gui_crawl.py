#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2019 tribe29 GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

# pylint: disable=redefined-outer-name,c-extension-no-member

import asyncio
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path
from typing import Deque, Generator, Iterable, MutableMapping, MutableSequence, Optional, Set, Tuple
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse, urlsplit, urlunsplit

import playwright.async_api
import pytest
import requests
from bs4 import BeautifulSoup  # type: ignore[import]
from lxml import etree
from playwright.async_api import async_playwright

from tests.testlib.site import get_site_factory, Site
from tests.testlib.version import CMKVersion
from tests.testlib.web_session import CMKWebSession

logger = logging.getLogger()


class Progress:
    def __init__(self, report_interval: float = 10) -> None:
        self.started = time.time()
        self.done_total = 0
        self.report_interval = report_interval
        self.next_report = 0.0

    def __enter__(self) -> "Progress":
        self.started = time.time()
        self.next_report = self.started + self.report_interval
        self.done_total = 0
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        logger.info(
            "%d done in %.3f secs %s",
            self.done_total,
            self.duration,
            "" if exc_type is None else f"(canceled with {exc_type})",
        )

    @property
    def duration(self) -> float:
        return time.time() - self.started

    def done(self, done: int) -> None:
        self.done_total += done
        if time.time() > self.next_report:
            logger.info(
                "rate: %.2f per sec (%d total)", self.done_total / self.duration, self.done_total
            )
            self.next_report = time.time() + self.report_interval


class InvalidUrl(Exception):
    def __init__(self, url: str, message: str) -> None:
        super().__init__(url, message)
        self.url = url
        self.message = message


class Url:
    def __init__(
        self,
        url: str,
        orig_url: Optional[str] = None,
        referer_url: Optional[str] = None,
        follow: bool = True,
    ) -> None:
        self.url = url
        self.orig_url = orig_url
        self.referer_url = referer_url
        self.follow = follow

    def __hash__(self) -> int:
        return hash(self.url)

    # Strip host and site prefix
    def neutral_url(self) -> str:
        return "check_mk/" + self.url.split("/check_mk/", 1)[1]

    # Strip proto and host
    def url_without_host(self) -> str:
        parsed = list(urlsplit(self.url))
        parsed[0] = ""
        parsed[1] = ""
        return urlunsplit(parsed)


class PageVisitor:
    Timeout: int = 60

    def __init__(self, web_session: CMKWebSession) -> None:
        self.web_session = web_session

    async def get_content_type(self, url: Url) -> str:
        def blocking():
            try:
                response = self.web_session.request(
                    "head", url.url_without_host(), timeout=self.Timeout
                )
            except requests.Timeout:
                raise TimeoutError(url)
            return response.headers.get("content-type")

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, blocking)

    async def get_text_and_logs(self, url: Url) -> Tuple[str, Iterable[str]]:
        def blocking():
            response = self.web_session.get(url.url_without_host())
            return response.text

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, blocking), []


class BrowserPageVisitor(PageVisitor):
    def __init__(
        self,
        web_session: CMKWebSession,
        browser: playwright.async_api.Browser,
        storage_state: playwright.async_api.StorageState,
    ) -> None:
        super().__init__(web_session)
        self.browser = browser
        self.storage_state = storage_state

    async def get_text_and_logs(self, url: Url) -> Tuple[str, Iterable[str]]:
        logs = []

        async def handle_console_messages(msg):
            location = (
                f"{msg.location['url']}:{msg.location['lineNumber']}:{msg.location['columnNumber']}"
            )
            logs.append(f"{msg.type}: {msg.text} ({location})")

        page = await self.browser.new_page(storage_state=self.storage_state)
        page.on("console", handle_console_messages)
        try:
            await page.goto(url.url, timeout=self.Timeout * 1000)
            text = await page.content()
        except playwright.async_api.TimeoutError:
            await page.close()
            raise TimeoutError(url)
        except playwright.async_api.Error as e:
            raise RuntimeError(f"{type(e)}: {e.message} ({e.name})")
        await page.close()
        return text, logs


async def create_web_session(site: Site) -> CMKWebSession:
    def blocking():
        web_session = CMKWebSession(site)
        # disable content parsing on each request for performance reasons
        web_session._handle_http_response = lambda *args, **kwargs: None  # type: ignore
        web_session.login()
        web_session.enforce_non_localized_gui()
        return web_session

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, blocking)


async def create_browser_and_context(
    site: Site,
) -> Tuple[playwright.async_api.Browser, playwright.async_api.StorageState]:
    pw = await async_playwright().start()
    browser = await pw.chromium.launch()

    context = await browser.new_context()
    page = await context.new_page()
    await page.goto(site.internal_url)
    await page.fill('input[name="_username"]', "cmkadmin")
    await page.fill('input[name="_password"]', "cmk")
    async with page.expect_navigation():
        await page.click("text=Login")
    await page.close()
    storage_state = await context.storage_state()
    await context.close()

    return browser, storage_state


async def create_visitor(site: Site, use_browser: bool) -> PageVisitor:
    web_session = await create_web_session(site)
    if use_browser:
        browser, storage_state = await create_browser_and_context(site)
        return BrowserPageVisitor(web_session, browser, storage_state)
    return PageVisitor(web_session)


@dataclass
class ErrorResult:
    message: str
    referer_url: Optional[str] = None


@dataclass
class CrawlSkipInfo:
    reason: str
    message: str


@dataclass
class CrawlResult:
    duration: float = 0.0
    skipped: Optional[CrawlSkipInfo] = None
    errors: MutableSequence[ErrorResult] = field(default_factory=list)


class Crawler:
    def __init__(self, site: Site, report_file: Optional[str]):
        self.duration = 0.0
        self.results: MutableMapping[str, CrawlResult] = {}
        self.site = site
        self.report_file = Path(report_file or self.site.result_dir() + "/crawl.xml")

        self._todos: Deque[Url] = deque([Url(self.site.internal_url)])

    def report(self) -> None:
        self.site.save_results()
        self._write_report_file()

        error_messages = list(
            chain.from_iterable(
                (
                    [
                        f"[{url} - found on {error.referer_url}] {error.message}"
                        for error in result.errors
                    ]
                    for url, result in self.results.items()
                    if result.errors
                )
            )
        )
        if error_messages:
            joined_error_messages = "\n".join(error_messages)
            raise Exception(
                f"Crawled {len(self.results)} URLs in {self.duration} seconds. Failures:\n{joined_error_messages}"
            )

    def _write_report_file(self) -> None:
        root = etree.Element("testsuites")
        testsuite = etree.SubElement(root, "testsuite")

        tests, errors, skipped = 0, 0, 0
        for url, result in self.results.items():
            testcase = etree.SubElement(
                testsuite,
                "testcase",
                attrib={
                    "name": url,
                    "classname": "crawled_urls",
                    "time": f"{result.duration:.3f}",
                },
            )
            if result.skipped is not None:
                skipped += 1
                etree.SubElement(
                    testcase,
                    "skipped",
                    attrib={
                        "type": result.skipped.reason,
                        "message": result.skipped.message,
                    },
                )
            elif result.errors:
                errors += 1
                for error in result.errors:
                    failure = etree.SubElement(
                        testcase, "failure", attrib={"message": error.message}
                    )
                    failure.text = f"referer_url: {error.referer_url}"

            tests += 1

        testsuite.attrib["name"] = "test-gui-crawl"
        testsuite.attrib["tests"] = str(tests)
        testsuite.attrib["skipped"] = str(skipped)
        testsuite.attrib["errors"] = str(errors)
        testsuite.attrib["failures"] = "0"
        testsuite.attrib["time"] = f"{self.duration:.3f}"
        testsuite.attrib["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")

        Path(self.report_file).write_bytes(etree.tostring(root, pretty_print=True))

    async def crawl(self, max_tasks: int = 10, use_browser: bool = True) -> None:
        visitor = await create_visitor(self.site, use_browser=use_browser)
        with Progress() as progress:
            tasks: Set = set()
            while tasks or self._todos:
                while self._todos and len(tasks) < max_tasks:
                    tasks.add(asyncio.create_task(self.visit_url(visitor, self._todos.popleft())))

                done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                progress.done(done=sum(1 for t in done if t.result()))
                self.duration = progress.duration

    def _ensure_result(self, url: Url) -> None:
        if url.url not in self.results:
            self.results[url.url] = CrawlResult()

    def handle_error(self, url: Url, error_type: str, message: str = "") -> bool:
        self._ensure_result(url)
        self.results[url.url].errors.append(
            ErrorResult(referer_url=url.referer_url, message=f"{error_type}: {message}")
        )
        logger.error("page error: %s: %s, (%s)", error_type, message, url.url)
        return True

    def handle_new_reference(self, url: Url, referer_url: Url) -> bool:
        if referer_url.follow and url.url not in self.results:
            self.results[url.url] = CrawlResult()
            self._todos.append(url)
            return True
        return False

    def handle_skipped_reference(self, url: Url, reason: str, message: str) -> None:
        self._ensure_result(url)
        if self.results[url.url].skipped is None:
            self.results[url.url].skipped = CrawlSkipInfo(
                reason=reason,
                message=message,
            )

    def handle_page_done(self, url: Url, duration: float) -> bool:
        self._ensure_result(url)
        self.results[url.url].duration = duration
        logger.info("page done in %.2f secs (%s)", duration, url.url)
        return self.results[url.url].skipped is None and len(self.results[url.url].errors) == 0

    async def visit_url(self, visitor: PageVisitor, url: Url) -> bool:
        start = time.time()
        try:
            content_type = await visitor.get_content_type(url)
        except TimeoutError:
            self.handle_error(url, "Timeout")
            return False

        if content_type.startswith("text/html"):
            try:
                text, logs = await visitor.get_text_and_logs(url)
                await self.validate(url, text, logs)
            except TimeoutError:
                self.handle_error(url, "Timeout")
            except RuntimeError as e:
                self.handle_error(url, "RuntimeError", repr(e))
        elif any(
            (content_type.startswith(ignored_start) for ignored_start in ["text/plain", "text/csv"])
        ):
            self.handle_skipped_reference(url, reason="content-type", message=content_type)
        elif content_type in [
            "application/x-rpm",
            "application/x-deb",
            "application/x-debian-package",
            "application/x-gzip",
            "application/x-msdos-program",
            "application/x-msi",
            "application/x-tgz",
            "application/x-redhat-package-manager",
            "application/x-pkg",
            "application/x-tar",
            "application/json",
            "application/pdf",
            "image/png",
            "image/gif",
            "text/x-chdr",
            "text/x-c++src",
            "text/x-sh",
        ]:
            self.handle_skipped_reference(url, reason="content-type", message=content_type)
        else:
            self.handle_error(url, error_type="UnknownContentType", message=content_type)

        return self.handle_page_done(url, duration=time.time() - start)

    async def validate(self, url: Url, text: str, logs: Iterable[str]) -> None:
        def blocking():
            soup = BeautifulSoup(text, "lxml")
            self.check_content(url, soup)
            self.check_links(url, soup)
            self.check_frames(url, soup)
            self.check_iframes(url, soup)
            self.check_logs(url, logs)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, blocking)

    def check_content(self, url: Url, soup: BeautifulSoup) -> None:
        if soup.find("div", id="login") is not None:
            self.handle_error(url, "LoginError", "login requested")

        ignore_texts = [
            "This view can only be used in mobile mode.",
            # Some single context views are accessed without their context information, which
            # results in a helpful error message since 1.7. These are not failures that this test
            # should report.
            "Missing context information",
            # Same for availability views that cannot be accessed any more
            # from views with missing context
            "miss some required context information",
            # Same for dashlets that are related to a specific context
            "There are no metrics meeting your context filters",
            # Some of the errors are only visible to the user when trying to submit and
            # some are visible for the reason that the GUI crawl sites do not have license
            # information configured -> ignore the errors
            "license usage report",
        ]
        for element in soup.select("div.error"):
            inner_html = str(element)
            if not any((ignore_text in inner_html for ignore_text in ignore_texts)):
                self.handle_error(url, "HtmlError", f"Found error: {inner_html}")

    def check_frames(self, url: Url, soup: BeautifulSoup) -> None:
        self.check_referenced(url, soup, "frame", "src")

    def check_iframes(self, url: Url, soup: BeautifulSoup) -> None:
        self.check_referenced(url, soup, "iframe", "src")

    def check_links(self, url: Url, soup: BeautifulSoup) -> None:
        self.check_referenced(url, soup, "a", "href")

    def check_referenced(self, referer_url: Url, soup: BeautifulSoup, tag: str, attr: str) -> None:
        elements = soup.find_all(tag)
        for element in elements:
            orig_url = element.get(attr)
            if orig_url is None:
                continue  # Skip elements that don't have the attribute in question
            normalized_orig_url = self.normalize_url(orig_url)
            if normalized_orig_url is None:
                continue
            url = Url(normalized_orig_url, orig_url=orig_url, referer_url=referer_url.url)
            try:
                self.verify_is_valid_url(url.url)
            except InvalidUrl as invalid_url:
                self.handle_skipped_reference(
                    url, reason="invalid-url", message=invalid_url.message
                )
            else:
                self.handle_new_reference(url, referer_url=referer_url)

    def check_logs(self, url: Url, logs: Iterable[str]):
        accepted_logs = [
            "Missing object for SimpleBar initiation.",
            "Error with Feature-Policy header: Unrecognized feature:",
        ]
        for log in logs:
            if not any(accepted_log in log for accepted_log in accepted_logs):
                self.handle_error(url, error_type="JavascriptError", message=log)

    def verify_is_valid_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "http":
            raise InvalidUrl(url, f"invalid scheme: {parsed.scheme}")
        # skip external urls
        if url.startswith("http://") and not url.startswith(self.site.internal_url):
            raise InvalidUrl(url, "external url")
        # skip non check_mk urls
        if (
            not parsed.path.startswith(f"/{self.site.id}/check_mk")
            or "../pnp4nagios/" in parsed.path
            or "../nagvis/" in parsed.path
            or "check_mk/plugin-api" in parsed.path
            or "../nagios/" in parsed.path
        ):
            raise InvalidUrl(url, "non Check_MK URL")
        # skip current url with link to index
        if "index.py?start_url=" in url:
            raise InvalidUrl(url, "link to index with current URL")
        if "logout.py" in url:
            raise InvalidUrl(url, "logout URL")
        if "_transid=" in url:
            raise InvalidUrl(url, "action URL")
        if "selection=" in url:
            raise InvalidUrl(url, "selection URL")
        # TODO: Remove this exclude when ModeCheckManPage works without an
        # automation call. Currently we have to use such a call to enrich the
        # man page with some additional info from config.check_info, see
        # AutomationGetCheckManPage.
        if "mode=check_manpage" in url and "wato.py" in url:
            raise InvalidUrl(url, "man page URL")
        # Don't follow filled in filter form views
        if "view.py" in url and "filled_in=filter" in url:
            raise InvalidUrl(url, "filled in filter URL")
        # Don't follow the view editor
        if "edit_view.py" in url:
            raise InvalidUrl(url, "view editor URL")
        # Skip agent download files
        if parsed.path.startswith(f"/{self.site.id}/check_mk/agents/"):
            raise InvalidUrl(url, "agent download file")

    def normalize_url(self, url: str) -> str:
        url = urljoin(self.site.internal_url, url.rstrip("#"))
        parsed = list(urlsplit(url))
        parsed[3] = urlencode(sorted(parse_qsl(parsed[3], keep_blank_values=True)))
        return urlunsplit(parsed)


class XssCrawler(Crawler):
    Payload = """javascript:/*--></title></style></textarea></script></xmp><svg/onload='+/"/+/onmouseover=1/+/[*/[]/+console.log("XSS vulnerability")//'>"""

    def handle_error(self, url: Url, error_type: str, message: str = "") -> bool:
        if error_type == "HtmlError":
            return False
        if error_type == "UnknownContentType" and message == "application/problem+json":
            return False
        return super().handle_error(url, error_type, message)

    def handle_page_done(self, url: Url, duration: float) -> bool:
        if super().handle_page_done(url, duration):
            for mutated_url in mutate_url_with_xss_payload(url, self.Payload):
                super().handle_new_reference(mutated_url, url)
            return True
        return False


def mutate_url_with_xss_payload(url: Url, payload: str) -> Generator[Url, None, None]:
    parsed_url = urlparse(url.url)
    parsed_query = parse_qs(parsed_url.query, keep_blank_values=True)
    for key, values in parsed_query.items():
        for change_idx in range(len(values)):
            mutated_values = [
                payload if idx == change_idx else value for idx, value in enumerate(values)
            ]
            mutated_query = {**parsed_query, key: mutated_values}
            mutated_url = parsed_url._replace(query=urlencode(mutated_query, doseq=True))
            yield Url(
                url=mutated_url.geturl(),
                referer_url=url.referer_url,
                orig_url=url.orig_url,
                follow=False,
            )


@pytest.fixture
def site() -> Site:
    version = os.environ.get("VERSION", CMKVersion.DAILY)
    sf = get_site_factory(
        prefix="crawl_", update_from_git=version == "git", install_test_python_modules=False
    )

    site = None
    if os.environ.get("REUSE", "0") == "1":
        site = sf.get_existing_site("central")
    if site is None or not site.exists():
        site = sf.get_site("central")
    logger.info("Site %s is ready!", site.id)

    return site


def test_crawl(site: Site) -> None:
    use_browser = os.environ.get("USE_BROWSER", "1") == "1"
    max_crawler_tasks = int(os.environ.get("GUI_CRAWLER_TASK_LIMIT", 10 if use_browser else 100))
    xss_crawl = os.environ.get("XSS_CRAWL", "0") == "1"

    crawler_type = XssCrawler if xss_crawl else Crawler

    crawler = crawler_type(site, report_file=os.environ.get("CRAWL_REPORT"))
    try:
        asyncio.run(crawler.crawl(max_tasks=max_crawler_tasks, use_browser=use_browser))
    finally:
        crawler.report()


@pytest.mark.type("unit")
@pytest.mark.parametrize(
    "url,payload,expected_urls",
    [
        ("http://host/page.py", "payload", []),
        ("http://host/page.py?key=", "payload", ["http://host/page.py?key=payload"]),
        ("http://host/page.py?key=value", "payload", ["http://host/page.py?key=payload"]),
        (
            "http://host/page.py?k1=v1&k2=v2",
            "payload",
            ["http://host/page.py?k1=payload&k2=v2", "http://host/page.py?k1=v1&k2=payload"],
        ),
        (
            "http://host/page.py?k1=v1&k1=v2",
            "payload",
            ["http://host/page.py?k1=payload&k1=v2", "http://host/page.py?k1=v1&k1=payload"],
        ),
    ],
)
def test_mutate_url_with_xss_payload(url: str, payload: str, expected_urls: Iterable[str]):
    assert [u.url for u in mutate_url_with_xss_payload(Url(url), payload)] == expected_urls


@pytest.mark.type("unit")
def test_mutate_url_with_xss_payload_url_metadata():
    url = Url(
        url="http://host/page.py?key=value",
        referer_url="http://host/referer.py",
        orig_url="http://host/orig.py",
    )
    mutated_url = list(mutate_url_with_xss_payload(url, "payload")).pop(0)
    assert mutated_url.referer_url == url.referer_url
    assert mutated_url.orig_url == url.orig_url
    assert not mutated_url.follow
