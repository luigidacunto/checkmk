// +------------------------------------------------------------------+
// |             ____ _               _        __  __ _  __           |
// |            / ___| |__   ___  ___| | __   |  \/  | |/ /           |
// |           | |   | '_ \ / _ \/ __| |/ /   | |\/| | ' /            |
// |           | |___| | | |  __/ (__|   <    | |  | | . \            |
// |            \____|_| |_|\___|\___|_|\_\___|_|  |_|_|\_\           |
// |                                                                  |
// | Copyright Mathias Kettner 2013             mk@mathias-kettner.de |
// +------------------------------------------------------------------+
//
// This file is part of Check_MK.
// The official homepage is at http://mathias-kettner.de/check_mk.
//
// check_mk is free software;  you can redistribute it and/or modify it
// under the  terms of the  GNU General Public License  as published by
// the Free Software Foundation in version 2.  check_mk is  distributed
// in the hope that it will be useful, but WITHOUT ANY WARRANTY;  with-
// out even the implied warranty of  MERCHANTABILITY  or  FITNESS FOR A
// PARTICULAR PURPOSE. See the  GNU General Public License for more de-
// ails.  You should have  received  a copy of the  GNU  General Public
// License along with GNU Make; see the file  COPYING.  If  not,  write
// to the Free Software Foundation, Inc., 51 Franklin St,  Fifth Floor,
// Boston, MA 02110-1301 USA.

// dashlet ids and urls
var reload_on_resize = {};

function size_dashlets() {
    var size_info = calculate_dashlets();
    var oDash = null;
    for (var d_number = 0; d_number < size_info.length; d_number++) {
        var dashlet = size_info[d_number];
        var d_visible = dashlet[0];
        var d_left    = dashlet[1];
        var d_top     = dashlet[2];
        var d_width   = dashlet[3];
        var d_height  = dashlet[4];

        var disstyle = "block";
        if (!d_visible) {
            disstyle = "none";
        }

        // check if dashlet has title and resize its width
        oDash = document.getElementById("dashlet_title_" + d_number);
        if (oDash) {
            //if browser window to small prevent js error
            if(d_width <= 20){
                d_width = 21;
            }
            oDash.style.width  = (d_width - 14) + "px";
            oDash.style.display = disstyle;
        }

        // resize outer div
        oDash = document.getElementById("dashlet_" + d_number);
        if(oDash) {
            oDash.style.display  = disstyle;
            oDash.style.left     = d_left + "px";
            oDash.style.top      = d_top + "px";
            oDash.style.width    = d_width + "px";
            oDash.style.height   = d_height + "px";
        }

        var netto_height = d_height - dashlet_padding[0] - dashlet_padding[2];
        var netto_width  = d_width  - dashlet_padding[1] - dashlet_padding[3];

        // resize content div
        oDash = document.getElementById("dashlet_inner_" + d_number);
        if(oDash) {
            oDash.style.display  = disstyle;
            oDash.style.left   = dashlet_padding[3] + "px";
            oDash.style.top    = dashlet_padding[0] + "px";
            if (netto_width > 0)
                oDash.style.width  = netto_width + "px";
            if (netto_height > 0)
                oDash.style.height = netto_height + "px";
        }

        if (typeof reload_on_resize[d_number] != 'undefined') {
            var base_url = reload_on_resize[d_number];
            var iframe = document.getElementById("dashlet_iframe_" + d_number);
            iframe.src = base_url + '&width=' + oDash.clientWidth
                         + '&height=' + oDash.clientHeight;
            iframe = null;
        }
    }
    oDash = null;
}

function vec(x, y) {
    this.x = x || 0;
    this.y = y || 0;
}

vec.prototype = {
    divide: function(v) {
        return new vec(~~(this.x / v.x), ~~(this.y / v.y));
    },
    add: function(v) {
        return new vec(this.x + v.x, this.y + v.y);
    },
    make_absolute: function(size_v) {
        return new vec(this.x < 0 ? this.x + size_v.x + 1 : this.x - 1,
                       this.y < 0 ? this.y + size_v.y + 1 : this.y - 1);
    },
    // Compute the initial size of the dashlet. If MAX is used,
    // then the dashlet consumes all space in its growing direction,
    // regardless of any other dashlets.
    initial_size: function(pos_v, grid_v) {
        return new vec(
            (this.x == MAX ? grid_v.x - Math.abs(pos_v.x) + 1 : (this.x == GROW ? 1 : this.x)),
            (this.y == MAX ? grid_v.y - Math.abs(pos_v.y) + 1 : (this.y == GROW ? 1 : this.y))
        );
    },
    // return codes:
    //  0: absolute size, no growth
    //  1: grow direction right, down
    // -1: grow direction left, up
    compute_grow_by: function(size_v) {
        return new vec(
            (size_v.x != GROW ? 0 : (this.x < 0 ? -1 : 1)),
            (size_v.y != GROW ? 0 : (this.y < 0 ? -1 : 1))
        );
    },
    toString: function() {
        return this.x+'/'+this.y;
    }
};

function calculate_dashlets() {
    var screen_size = new vec(g_dashboard_width, g_dashboard_height);
    var raster_size = screen_size.divide(grid_size);
    var used_matrix = {};
    var positions   = [];

    // first place all dashlets at their absolute positions
    for (var nr = 0; nr < dashlets.length; nr++) {
        var dashlet = dashlets[nr];
        
        // Relative position is as noted in the declaration. 1,1 => top left origin,
        // -1,-1 => bottom right origin, 0 is not allowed here
        // starting from 1, negative means: from right/bottom
        var rel_position = new vec(dashlet.x, dashlet.y);

        // Compute the absolute position, this time from 0 to raster_size-1
        var abs_position = rel_position.make_absolute(raster_size);

        // The size in raster-elements. A 0 for a dimension means growth. No negative values here.
        var size = new vec(dashlet.w, dashlet.h);

        // Compute the minimum used size for the dashlet. For growth-dimensions we start with 1
        var used_size = size.initial_size(rel_position, raster_size);

        // Now compute the rectangle that is currently occupied. The choords
        // of bottomright are *not* included.
        var top, left, right, bottom;
        if (rel_position.x > 0) {
            left = abs_position.x;
            right = left + used_size.x;
        }
        else {
            right = abs_position.x;
            left = right - used_size.x;
        }

        if (rel_position.y > 0) {
            top = abs_position.y
            bottom = top + used_size.y
        }
        else {
            bottom = abs_position.y;
            top = bottom - used_size.y;
        }

        // Allocate used squares in matrix. If not all squares we need are free,
        // then the dashboard is too small for all dashlets (as it seems).
        // TEST: Dashlet auf 0/0 setzen, wenn kein Platz dafür da ist.
        try {
            for (var x = left; x < right; x++) {
                for (var y = top; y < bottom; y++) {
                    if (x+' '+y in used_matrix) {
                        throw 'used';
                    }
                    used_matrix[x+' '+y] = true;
                }
            }
            // Helper variable for how to grow, both x and y in [-1, 0, 1]
            var grow_by = rel_position.compute_grow_by(size);

            positions.push([true, left, top, right, bottom, grow_by]);
        } catch (e) {
            if (e == 'used')
                positions.push([true, left, top, right, bottom, new vec(0, 0)]);
            else
                throw e;
        }
    }

    var try_allocate = function(left, top, right, bottom) {
        // Try if all needed squares are free
        for (var x = left; x < right; x++)
            for (var y = top; y < bottom; y++)
                if (x+' '+y in used_matrix)
                    return false;

        // Allocate all needed squares
        for (var x = left; x < right; x++)
            for (var y = top; y < bottom; y++)
                used_matrix[x+' '+y] = true;

        return true;
    };

    // Now try to expand all elastic rectangles as far as possible
    // FIXME: Das hier muesste man optimieren
    var at_least_one_expanded = true;
    while (at_least_one_expanded) {
        at_least_one_expanded = false;
        var new_positions = []
        for (var nr = 0; nr < positions.length; nr++) {
            var visible = positions[nr][0],
                left    = positions[nr][1],
                top     = positions[nr][2],
                right   = positions[nr][3],
                bottom  = positions[nr][4],
                grow_by = positions[nr][5];

            if (visible) {
                // try to grow in X direction by one
                if (grow_by.x > 0 && right < raster_size.x && try_allocate(right, top, right+1, bottom)) {
                    at_least_one_expanded = true;
                    right += 1;
                }
                else if (grow_by.x < 0 && left > 0 && try_allocate(left-1, top, left, bottom)) {
                    at_least_one_expanded = true;
                    left -= 1;
                }

                // try to grow in Y direction by one
                if (grow_by.y > 0 && bottom < raster_size.y && try_allocate(left, bottom, right, bottom+1)) {
                    at_least_one_expanded = true;
                    bottom += 1;
                }
                else if (grow_by.y < 0 && top > 0 && try_allocate(left, top-1, right, top)) {
                    at_least_one_expanded = true;
                    top -= 1;
                }
            }
            new_positions.push([visible, left, top, right, bottom, grow_by]);
        }
        positions = new_positions;
    }

    var size_info = [];
    for (var nr = 0; nr < positions.length; nr++) {
        var visible = positions[nr][0],
            left    = positions[nr][1],
            top     = positions[nr][2],
            right   = positions[nr][3],
            bottom  = positions[nr][4];
        size_info.push([
            visible,
            left * grid_size.x,
            top * grid_size.y,
            (right - left) * grid_size.x,
            (bottom - top) * grid_size.y 
        ]);
    }
    return size_info;
}

var g_dashboard_resizer = null;
var g_dashboard_top     = null
var g_dashboard_left    = null
var g_dashboard_width   = null;
var g_dashboard_height  = null;

function calculate_dashboard() {
    if (g_dashboard_resizer !== null)
        return; // another resize is processed
    g_dashboard_resizer = true;

    g_dashboard_top    = header_height + screen_margin;
    g_dashboard_left   = screen_margin;
    g_dashboard_width  = pageWidth() - 2*screen_margin;
    g_dashboard_height = pageHeight() - 2*screen_margin - header_height;

    var oDash = document.getElementById("dashboard");
    oDash.style.left     = g_dashboard_left + "px";
    oDash.style.top      = g_dashboard_top + "px";
    oDash.style.width    = g_dashboard_width + "px";
    oDash.style.height   = g_dashboard_height + "px";

    size_dashlets();
    g_dashboard_resizer = null;
}

function dashboard_scheduler(initial) {
    var timestamp = Date.parse(new Date()) / 1000;
    var newcontent = "";
    for(var i = 0; i < refresh_dashlets.length; i++) {
        var nr      = refresh_dashlets[i][0];
        var refresh = refresh_dashlets[i][1];
        var url     = refresh_dashlets[i][2];

        if ((initial && document.getElementById("dashlet_inner_" + nr).innerHTML == '')
                || (refresh > 0 && timestamp % refresh == 0)) {
            get_url(url, dashboard_update_contents, "dashlet_inner_" + nr);
        }
    }
    setTimeout(function() { dashboard_scheduler(0); }, 1000);
    // Update timestamp every minute
    // Required if there are no refresh_dashlets present or all refresh times are > 60sec
    if (timestamp % 60 == 0) {
        updateHeaderTime();
    }
}

function dashboard_update_contents(id, code) {
    // Update the header time
    updateHeaderTime();

    // Call the generic function to replace the dashlet inner code
    updateContents(id, code);
}

function update_dashlet(id, code) {
  var obj = document.getElementById(id);
  if (obj) {
    obj.innerHTML = code;
    executeJS(id);
    obj = null;
  }
}

//
// DASHBOARD EDITING
//

function toggle_dashboard_controls(show) {
    var controls = document.getElementById('controls');
    if (!controls)
        return; // maybe not permitted -> skip

    if (show === undefined)
        var show = controls.style.display != 'block';
    controls.style.display = show ? 'block' : 'none';
}

var g_editing = false;

function toggle_dashboard_edit() {
    // First hide the controls menu
    toggle_dashboard_controls(false);

    g_editing = !g_editing;

    document.getElementById('control_edit').style.display = !g_editing ? 'block' : 'none';
    document.getElementById('control_view').style.display = g_editing ? 'block' : 'none';

    var dashlet_divs = document.getElementsByClassName('dashlet');
    for(var i = 0; i < dashlet_divs.length; i++)
        dashlet_toggle_edit(dashlet_divs[i]);

    toggle_grid();
}

function toggle_grid() {
    if (!g_editing) {
        remove_class(document.getElementById('dashboard'), 'grid');
    } else {
        add_class(document.getElementById('dashboard'), 'grid');
    }
}

function active_anchor(coords) {
    var active = 0;
    if (coords.x < 0 && coords.y >= 0)
        active = 1;
    else if (coords.x < 0 && coords.y < 0)
        active = 2;
    else if (coords.x >= 0 && coords.y < 0)
        active = 3;
    return active
}

function dashlet_toggle_edit(dashlet, edit) {
    var id = parseInt(dashlet.id.replace('dashlet_', ''));
    var inner = document.getElementById('dashlet_inner_'+id);
    var coords = dashlets[id];

    var edit = edit === undefined ? g_editing : edit;

    if (edit) {
        // gray out the inner parts of the dashlet
        add_class(dashlet, 'edit');

        // Create the dashlet controls
        var controls = document.createElement('div');
        controls.setAttribute('id', 'dashlet_controls_'+id);
        controls.className = 'controls';
        dashlet.appendChild(controls);

        // Which is the anchor corner?
        // 0: topleft, 1: topright, 2: bottomright, 3: bottomleft
        var active = active_anchor(coords);

        // Create the size / grow indicators
        for (var i = 0; i < 2; i ++) {
            // 0 ~ X, 1 ~ Y
            var sizer = document.createElement('div');
            sizer.className = 'sizer sizer'+i+' anchor'+active;

            // create the sizer label
            var sizer_lbl = document.createElement('div');
            sizer_lbl.className = 'sizer_lbl sizer_lbl'+i+' anchor'+active;

            if (i == 0 && coords.w == MAX) {
                sizer.className += ' max';
                sizer_lbl.innerHTML = 'MAX';
            }
            else if (i == 0 && coords.w == GROW) {
                sizer.className += ' grow';
                sizer_lbl.innerHTML = 'GROW';
            }
            else if (i == 1 && coords.h == MAX) {
                sizer.className += ' max';
                sizer_lbl.innerHTML = 'MAX';
            }
            else if (i == 1 && coords.h == GROW) {
                sizer.className += ' grow';
                sizer_lbl.innerHTML = 'GROW';
            }
            else if (i == 0) {
                sizer.className += ' abs';
                sizer_lbl.innerHTML = coords.w;
            }
            else if (i == 1) {
                sizer.className += ' abs';
                sizer_lbl.innerHTML = coords.h;
            }

            // js magic stuff - closures!
            sizer.onclick = function(dashlet_id, sizer_id) {
                return function() {
                    toggle_sizer(dashlet_id, sizer_id);
                };
            }(id, i);

            controls.appendChild(sizer);
            controls.appendChild(sizer_lbl);
        }

        // Create the anchors
        for (var i = 0; i < 4; i++) {
            var anchor = document.createElement('a');
            anchor.className = 'anchor anchor'+i;
            if (active != i)
                anchor.className += ' off';

            // js magic stuff - closures!
            anchor.onclick = function(dashlet_id, anchor_id) {
                return function() {
                    toggle_anchor(dashlet_id, anchor_id);
                };
            }(id, i);

            controls.appendChild(anchor);
        }
    } else {
        // make the inner parts visible again
        remove_class(dashlet, 'edit');

        // Remove all dashlet controls
        var controls = document.getElementById('dashlet_controls_'+id);
        controls.parentNode.removeChild(controls);
    }
}

function toggle_sizer(nr, sizer_id) {
    var dashlet = dashlets[nr];
    var dashlet_obj = document.getElementById('dashlet_'+nr);

    if (sizer_id == 0) {
        if (dashlet.w > 0)
            dashlet.w = GROW;
        else if (dashlet.w == GROW)
            dashlet.w = MAX;
        else if (dashlet.w == MAX)
            dashlet.w = dashlet_obj.clientWidth / grid_size.x;
    }
    else {
        if (dashlet.h > 0)
            dashlet.h = GROW;
        else if (dashlet.h == GROW)
            dashlet.h = MAX;
        else if (dashlet.h == MAX)
            dashlet.h = dashlet_obj.clientHeight / grid_size.y;
    }

    rerender_dashlet_controls(dashlet_obj);
    size_dashlets();
}

function toggle_anchor(nr, anchor_id) {
    var dashlet = dashlets[nr];
    var old_x = dashlets[nr].x;
    var old_y = dashlets[nr].y;

    if (anchor_id == 0 && dashlet.x > 0 && dashlet.y > 0
        || anchor_id == 1 && dashlet.x <= 0 && dashlet.y > 0
        || anchor_id == 2 && dashlet.x <= 0 && dashlet.y <= 0
        || anchor_id == 3 && dashlet.x > 0 && dashlet.y <= 0)
        return; // anchor has not changed, skip it!

    // We do not want to recompute the dimensions of growing dashlets here,
    // use the current effective size
    var dashlet_obj = document.getElementById('dashlet_' + nr);
    var width  = dashlet_obj.clientWidth / grid_size.x; 
    var height = dashlet_obj.clientHeight / grid_size.y; 

    var screen_size  = new vec(g_dashboard_width, g_dashboard_height);
    var raster_size  = screen_size.divide(grid_size);
    var size         = new vec(width, height);
    var rel_position = new vec(dashlet.x, dashlet.y);
    var abs_position = rel_position.make_absolute(raster_size);
    var topleft_pos  = new vec(rel_position.x > 0 ? abs_position.x : abs_position.x - size.x,
                               rel_position.y > 0 ? abs_position.y : abs_position.y - size.y);

    if (anchor_id == 0) {
        dashlet.x = topleft_pos.x;
        dashlet.y = topleft_pos.y;
    }
    else if (anchor_id == 1) {
        dashlet.x = (topleft_pos.x + size.x) - (raster_size.x + 2);
        dashlet.y = topleft_pos.y
    }
    else if (anchor_id == 2) {
        dashlet.x = (topleft_pos.x + size.x) - (raster_size.x + 2);
        dashlet.y = (topleft_pos.y + size.y) - (raster_size.y + 2);
    }
    else if (anchor_id == 3) {
        dashlet.x = topleft_pos.x;
        dashlet.y = (topleft_pos.y + size.y) - (raster_size.y + 2);
    }
    dashlet.x += 1;
    dashlet.y += 1;

    // Visualize the change within the dashlet
    rerender_dashlet_controls(dashlet_obj);

    // Apply the change to all rendered dashlets
    size_dashlets();

    // FIXME: Persist change
}

function rerender_dashlet_controls(dashlet_obj) {
    dashlet_toggle_edit(dashlet_obj, false);
    dashlet_toggle_edit(dashlet_obj, true);
}

var g_dragging = false;
var g_orig_pos = null;
var g_mouse_offset = null;

function drag_dashlet_start(event) {
    if (!event)
        event = window.event;

    if (!g_editing)
        return true;

    var target = getTarget(event);
    var button = getButton(event);

    if (g_dragging === false && button == 'LEFT' && has_class(target, 'controls')) {
        if (event.preventDefault)
            event.preventDefault();
        if (event.stopPropagation)
            event.stopPropagation();
        event.returnValue = false;

        g_dragging = target.parentNode;
        g_orig_pos = [ target.parentNode.offsetLeft, target.parentNode.offsetTop ];
        g_mouse_offset = [
            event.clientX - target.parentNode.offsetLeft,
            event.clientY - target.parentNode.offsetTop
        ];

        return false;
    }
    return true;
}

function drag_dashlet(event) {
    if (!event)
        event = window.event;
    
    if (!g_dragging)
        return true;

    // position of the dashlet
    var x = ~~((event.clientX - g_mouse_offset[0]) / 10) * 10;
    var y = ~~((event.clientY - g_mouse_offset[1]) / 10) * 10;

    // convert x/y coords to grid coords and save info in dashlets construct
    var nr = parseInt(g_dragging.id.replace('dashlet_', ''));
    if (dashlets[nr].x == x / 10 + 1 && dashlets[nr].y == y / 10 + 1) {
        return; // skip non movement!
    }

    // Prevent dragging out of screen
    //if (x < 0)
    //    x = 0;
    //if (y < 0)
    //    y = 0;
    //if (x + g_dragging.clientWidth >= ~~((g_dashboard_left + g_dashboard_width) / 10) * 10)
    //    x = ~~((g_dashboard_width - g_dragging.clientWidth) / 10) *  10;
    //if (y + g_dragging.clientHeight >= ~~((g_dashboard_top + g_dashboard_height) / 10) * 10)
    //    y = ~~((g_dashboard_height - g_dragging.clientHeight) / 10) * 10;

    dashlets[nr].x = x / 10 + 1;
    dashlets[nr].y = y / 10 + 1;

    size_dashlets();
}

function drag_dashlet_stop(event) {
    if (!event)
        event = window.event;

    g_dragging = false;

    // FIXME: When dashlet is out of screen, snap back to the original position

    // FIXME: Persist the change

    return false;
}

// First firefox and then IE
if (window.addEventListener) {
    window.addEventListener("mousemove",     function(e) {
                                               drag_dashlet(e);
                                               return false;
                                             }, false);
    window.addEventListener("mousedown",     drag_dashlet_start, false);
    window.addEventListener("mouseup",       drag_dashlet_stop,  false);
} else {
    document.documentElement.onmousemove  = function(e) {
      drag_dashlet(e);
      return false;
    };
    document.documentElement.onmousedown  = drag_dashlet_start;
    document.documentElement.onmouseup    = drag_dashlet_stop;
}
