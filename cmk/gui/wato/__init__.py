#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

# WATO
#
# This file contain actual page handlers and Setup modes. It does HTML creation
# and implement AJAX handlers. It uses classes, functions and globals
# from watolib.py.

#   .--README--------------------------------------------------------------.
#   |               ____                _                                  |
#   |              |  _ \ ___  __ _  __| |  _ __ ___   ___                 |
#   |              | |_) / _ \/ _` |/ _` | | '_ ` _ \ / _ \                |
#   |              |  _ <  __/ (_| | (_| | | | | | | |  __/                |
#   |              |_| \_\___|\__,_|\__,_| |_| |_| |_|\___|                |
#   |                                                                      |
#   +----------------------------------------------------------------------+
#   | A few words about the implementation details of Setup.                |
#   `----------------------------------------------------------------------'

# [1] Files and Folders
# Setup organizes hosts in folders. A wato folder is represented by a
# OS directory. If the folder contains host definitions, then in that
# directory a file name "hosts{.mk|.cfg}" is kept.
# The directory hierarchy of Setup is rooted at etc/check_mk/conf.d/wato.
# All files in and below that directory are kept by Setup. Setup does not
# touch any other files or directories in conf.d.
# A *path* in Setup means a relative folder path to that directory. The
# root folder has the empty path (""). Folders are separated by slashes.
# Each directory contains a file ".wato" which keeps information needed
# by Setup but not by Checkmk itself.

# [3] Convention for variable names:
# site_id     --> The id of a site, None for the local site in non-distributed setup
# site        --> The dictionary datastructure of a site
# host_name   --> A string containing a host name
# host        --> An instance of the class Host
# folder_path --> A relative specification of a folder (e.g. "linux/prod")
# folder      --> An instance of the class Folder

# .
#   .--Init----------------------------------------------------------------.
#   |                           ___       _ _                              |
#   |                          |_ _|_ __ (_) |_                            |
#   |                           | || '_ \| | __|                           |
#   |                           | || | | | | |_                            |
#   |                          |___|_| |_|_|\__|                           |
#   |                                                                      |
#   +----------------------------------------------------------------------+
#   | Importing, Permissions, global variables                             |
#   `----------------------------------------------------------------------'

# A huge number of imports are here to be compatible with old GUI plugins. Once we dropped support
# for them, we can remove this here and the imports
# flake8: noqa
# pylint: disable=unused-import
from typing import Any

import cmk.utils.paths
import cmk.utils.version as cmk_version
from cmk.utils.exceptions import MKGeneralException

import cmk.gui.background_job as background_job
import cmk.gui.forms as forms
import cmk.gui.gui_background_job as gui_background_job
import cmk.gui.sites as sites
import cmk.gui.userdb as userdb
import cmk.gui.utils as utils
import cmk.gui.valuespec
import cmk.gui.view_utils
import cmk.gui.watolib as watolib
import cmk.gui.watolib.attributes
import cmk.gui.watolib.changes
import cmk.gui.watolib.config_domain_name
import cmk.gui.watolib.config_hostname
import cmk.gui.watolib.host_attributes
import cmk.gui.watolib.hosts_and_folders
import cmk.gui.watolib.rulespecs
import cmk.gui.watolib.sites
import cmk.gui.watolib.timeperiods
import cmk.gui.watolib.translation
import cmk.gui.watolib.user_scripts
import cmk.gui.watolib.utils
import cmk.gui.weblib as weblib
from cmk.gui.cron import register_job
from cmk.gui.htmllib.html import html
from cmk.gui.i18n import _
from cmk.gui.log import logger
from cmk.gui.pages import Page, page_registry
from cmk.gui.permissions import Permission, permission_registry
from cmk.gui.table import table_element
from cmk.gui.type_defs import PermissionName
from cmk.gui.utils.html import HTML
from cmk.gui.visuals.filter import FilterRegistry
from cmk.gui.watolib.activate_changes import update_config_generation

if cmk_version.edition() is cmk_version.Edition.CME:
    import cmk.gui.cme.managed as managed  # pylint: disable=no-name-in-module
else:
    managed = None  # type: ignore[assignment]

from cmk.gui.plugins.wato.utils import (
    Levels,
    monitoring_macro_help,
    PredictiveLevels,
    register_hook,
    RulespecGroupCheckParametersApplications,
    RulespecGroupCheckParametersDiscovery,
    RulespecGroupCheckParametersEnvironment,
    RulespecGroupCheckParametersHardware,
    RulespecGroupCheckParametersNetworking,
    RulespecGroupCheckParametersOperatingSystem,
    RulespecGroupCheckParametersPrinters,
    RulespecGroupCheckParametersStorage,
    RulespecGroupCheckParametersVirtualization,
    UserIconOrAction,
)
from cmk.gui.watolib.rulespecs import register_check_parameters as register_check_parameters
from cmk.gui.watolib.translation import HostnameTranslation

# Has to be kept for compatibility with pre 1.6 register_rule() and register_check_parameters()
# calls in the Setup plugin context
subgroup_networking = RulespecGroupCheckParametersNetworking().sub_group_name
subgroup_storage = RulespecGroupCheckParametersStorage().sub_group_name
subgroup_os = RulespecGroupCheckParametersOperatingSystem().sub_group_name
subgroup_printing = RulespecGroupCheckParametersPrinters().sub_group_name
subgroup_environment = RulespecGroupCheckParametersEnvironment().sub_group_name
subgroup_applications = RulespecGroupCheckParametersApplications().sub_group_name
subgroup_virt = RulespecGroupCheckParametersVirtualization().sub_group_name
subgroup_hardware = RulespecGroupCheckParametersHardware().sub_group_name
subgroup_inventory = RulespecGroupCheckParametersDiscovery().sub_group_name

import cmk.gui.watolib.config_domains

# Make some functions of watolib available to Setup plugins without using the
# watolib module name. This is mainly done for compatibility reasons to keep
# the current plugin API functions working
import cmk.gui.watolib.network_scan
import cmk.gui.watolib.read_only
from cmk.gui.valuespec import Age as Age
from cmk.gui.valuespec import Alternative as Alternative
from cmk.gui.valuespec import Dictionary as Dictionary
from cmk.gui.valuespec import Filesize as Filesize
from cmk.gui.valuespec import FixedValue as FixedValue
from cmk.gui.valuespec import ListOfStrings as ListOfStrings
from cmk.gui.valuespec import MonitoredHostname as MonitoredHostname
from cmk.gui.valuespec import MonitoringState as MonitoringState
from cmk.gui.valuespec import Password as Password
from cmk.gui.valuespec import Percentage as Percentage
from cmk.gui.valuespec import RegExpUnicode as RegExpUnicode
from cmk.gui.valuespec import TextAscii as TextAscii
from cmk.gui.valuespec import TextUnicode as TextUnicode
from cmk.gui.valuespec import Transform as Transform
from cmk.gui.wato._main_module_topics import MainModuleTopicAgents as MainModuleTopicAgents
from cmk.gui.wato._main_module_topics import MainModuleTopicEvents as MainModuleTopicEvents
from cmk.gui.wato._main_module_topics import MainModuleTopicExporter as MainModuleTopicExporter
from cmk.gui.wato._main_module_topics import MainModuleTopicGeneral as MainModuleTopicGeneral
from cmk.gui.wato._main_module_topics import MainModuleTopicHosts as MainModuleTopicHosts
from cmk.gui.wato._main_module_topics import (
    MainModuleTopicMaintenance as MainModuleTopicMaintenance,
)
from cmk.gui.wato._main_module_topics import MainModuleTopicServices as MainModuleTopicServices
from cmk.gui.wato._main_module_topics import MainModuleTopicUsers as MainModuleTopicUsers
from cmk.gui.wato.page_handler import page_handler
from cmk.gui.watolib.hosts_and_folders import ajax_popup_host_action_menu
from cmk.gui.watolib.main_menu import MenuItem, register_modules, WatoModule
from cmk.gui.watolib.mode import mode_registry, mode_url, redirect, WatoMode
from cmk.gui.watolib.rulespecs import register_rule as register_rule
from cmk.gui.watolib.sites import LivestatusViaTCP

from ._check_plugin_selection import CheckPluginSelection as CheckPluginSelection
from ._group_selection import ContactGroupSelection as ContactGroupSelection
from ._group_selection import HostGroupSelection as HostGroupSelection
from ._group_selection import ServiceGroupSelection as ServiceGroupSelection
from ._notification_parameter import (
    notification_parameter_registry as notification_parameter_registry,
)
from ._notification_parameter import NotificationParameter as NotificationParameter
from ._notification_parameter import NotificationParameterRegistry as NotificationParameterRegistry
from ._notification_parameter import register_notification_parameters
from ._permissions import PermissionSectionWATO as PermissionSectionWATO
from .pages._match_conditions import FullPathFolderChoice as FullPathFolderChoice
from .pages._match_conditions import (
    multifolder_host_rule_match_conditions as multifolder_host_rule_match_conditions,
)
from .pages._password_store_valuespecs import (
    MigrateNotUpdatedToIndividualOrStoredPassword as MigrateNotUpdatedToIndividualOrStoredPassword,
)
from .pages._rule_conditions import DictHostTagCondition as DictHostTagCondition
from .pages._rule_conditions import LabelCondition as LabelCondition
from .pages._simple_modes import SimpleEditMode as SimpleEditMode
from .pages._simple_modes import SimpleListMode as SimpleListMode
from .pages._simple_modes import SimpleModeType as SimpleModeType
from .pages._tile_menu import TileMenuRenderer as TileMenuRenderer

# .
#   .--Plugins-------------------------------------------------------------.
#   |                   ____  _             _                              |
#   |                  |  _ \| |_   _  __ _(_)_ __  ___                    |
#   |                  | |_) | | | | |/ _` | | '_ \/ __|                   |
#   |                  |  __/| | |_| | (_| | | | | \__ \                   |
#   |                  |_|   |_|\__,_|\__, |_|_| |_|___/                   |
#   |                                 |___/                                |
#   +----------------------------------------------------------------------+
#   | Prepare plugin-datastructures and load Setup plugins                  |
#   '----------------------------------------------------------------------'

modes: dict[str, Any] = {}


def load_plugins() -> None:
    """Plugin initialization hook (Called by cmk.gui.main_modules.load_plugins())"""
    # Initialize watolib things which are needed before loading the Setup plugins.
    # This also loads the watolib plugins.
    watolib.load_watolib_plugins()

    utils.load_web_plugins("wato", globals())

    if modes:
        raise MKGeneralException(
            _("Deprecated Setup modes found: %r. They need to be refactored to new API.")
            % list(modes.keys())
        )
