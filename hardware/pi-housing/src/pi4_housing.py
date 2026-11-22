"""A two-part housing for a Raspberry Pi 4 Model B, FDM printed in PLA.

The base is a tray with four standoffs; the lid is a cap whose four posts clamp the
board onto them. Four M2.5 screws go up through the base and the board's mounting
holes and form their own thread in the lid posts. Every port opening is a notch in
a wall's rim, so both parts print without supports: the base floor down, the lid top
face down. `base` and `lid` are modelled as they lie on one build plate, side by side;
`housing` puts the lid back on the base.

Coordinates of the assembled housing: the board's corner at the microSD end on the
USB-C side is x = y = 0, x runs to the USB/Ethernet end, y to the GPIO side, and
z = 0 is the underside of the base. Board dimensions are Raspberry Pi's mechanical
drawing RP-001185-MD-1.

Check: cad model dfm "$ID" --build-volume 256x256x256
"""

import build123d as bd
from bd_materials import plastics
from bd_materials.processes import fdm
from cad import model

# The board (RP-001185-MD-1).
PCB_X = 85.0
PCB_Y = 56.0
PCB_T = 1.5  # nominal; the seam between the walls takes up 1.4 to 1.7 mm boards
PCB_CORNER_R = 3.0
HOLES = ((3.5, 3.5), (61.5, 3.5), (3.5, 52.5), (61.5, 52.5))
TALLEST_PART = 16.0  # the USB stacks, above the board's top face

# The shell.
# Heights are multiples of the 0.2 mm layer, so no face falls inside a layer.
CLEARANCE = 0.75  # board edge to the inside of the walls
SD_END_CLEARANCE = 2.6  # more at the microSD end, so the card stays inside the wall
WALL = 2.4
FLOOR = 2.0
TOP = 2.0
STANDOFF_H = 3.6  # room for the microSD socket and through-hole pins under the board
HEADROOM = TALLEST_PART + 2.0
SEAM = 0.3  # gap left between the walls, so the screws clamp the board, not the walls
BELOW_BOARD_TOP = 0.5  # the base walls stop this far under the board's top face

PCB_TOP = FLOOR + STANDOFF_H + PCB_T
BASE_WALL_TOP = PCB_TOP - BELOW_BOARD_TOP
LID_RIM = BASE_WALL_TOP + SEAM
CEILING = PCB_TOP + HEADROOM
HEIGHT = CEILING + TOP

# Screws: M2.5 x 10 to 16 mm, pan or cap head, from below.
SCREW_CLEAR_D = 2.9
SCREW_PILOT_D = 2.1  # the screw forms its thread in the lid post
SCREW_PILOT_DEPTH = 12.0
HEAD_D = 5.4
HEAD_DEPTH = 2.6  # a cap head is 2.5 high
HEAD_MOUTH_CHAMFER = 0.4  # keeps the recess to size where the first layers spread
STANDOFF_TOP_D = 6.0  # the board's pad round each hole is 6 mm
STANDOFF_BASE_D = 8.0
STANDOFF_FOOT_H = 2.0  # above this the standoff stands 0.8 mm or more off the wall
POST_D = 5.4  # inside the 6 mm pad, clear of the USB-C socket and the GPIO header
POST_WEB = 2.0

# Openings: (from, to along the wall, height above the board's top face).
# The side openings admit a plug's overmould, not just the connector's shell.
SIDE_PORTS = (
    (4.95, 17.45, 5.4),  # USB-C power, centred on x = 11.2
    (20.25, 31.75, 5.4),  # micro HDMI 0, x = 26.0
    (33.75, 45.25, 5.4),  # micro HDMI 1, x = 39.5
    (49.25, 58.75, 7.8),  # 3.5 mm audio/video, x = 54.0
)
# One opening for the whole connector end: pillars between the jacks would be under
# 2 mm wide and hang free from the top, so they are left out.
END_PORTS = (
    (0.9, 35.1, 17.0),  # both USB stacks (y = 9 and 27) with their flared rims
    (35.1, 54.25, 14.2),  # Ethernet, y = 45.75
)
THROUGH = 1.0  # how far an opening's cut runs past each face of its wall
# A square bite out of the end wall and the floor under the microSD card, for a fingertip.
SD_Y = 28.0
SD_NOTCH_W = 14.5
SD_NOTCH_REACH = 0.5  # how far under the board it runs

END_VENTS = tuple(10.0 + 6.0 * i for i in range(7))  # y of each slot in the microSD end wall
END_VENT_W = 2.0
END_VENT_H = 9.0  # above the board's top face
TOP_VENTS = tuple(15.0 + 5.0 * i for i in range(8))  # x of each slot in the lid's top
TOP_VENT_W = 2.0
TOP_VENT_L = 26.0
TOP_VENT_Y = 28.0

EDGE_CHAMFER = 0.8

X0 = -(SD_END_CLEARANCE + WALL)
X1 = PCB_X + CLEARANCE + WALL
Y0 = -(CLEARANCE + WALL)
Y1 = PCB_Y + CLEARANCE + WALL

# The lid as printed is the assembled lid turned over about x, top face down, and set
# beside the base.
LID_TURN = (0, PCB_Y, HEIGHT)
PLATE_PITCH = (Y1 - Y0) + 10.0


def plate(z0, z1, inset=0.0):
    """The housing's footprint between two heights, shrunk by `inset` all round."""
    profile = bd.RectangleRounded(
        X1 - X0 - 2 * inset, Y1 - Y0 - 2 * inset, PCB_CORNER_R + CLEARANCE + WALL - inset
    )
    centre = ((X0 + X1) / 2, (Y0 + Y1) / 2, z0)
    return bd.Pos(*centre) * bd.extrude(profile, z1 - z0)


def box(x0, x1, y0, y1, z0, z1):
    lo = (bd.Align.MIN, bd.Align.MIN, bd.Align.MIN)
    return bd.Pos(x0, y0, z0) * bd.Box(x1 - x0, y1 - y0, z1 - z0, align=lo)


def cylinder(x, y, z0, z1, d):
    lo = (bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    return bd.Pos(x, y, z0) * bd.Cylinder(d / 2, z1 - z0, align=lo)


def cone(x, y, z0, z1, d0, d1):
    lo = (bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    return bd.Pos(x, y, z0) * bd.Cone(d0 / 2, d1 / 2, z1 - z0, align=lo)


def finish(body, label, color):
    body.label = label
    body.material = plastics.pla(process=fdm(layer_height_mm=0.2))
    body.color = color
    return body


def base_body():
    body = plate(0, BASE_WALL_TOP) - plate(FLOOR, BASE_WALL_TOP, inset=WALL)
    body = bd.chamfer(body.edges().group_by(bd.Axis.Z)[0], EDGE_CHAMFER)
    for x, y in HOLES:
        body += cone(x, y, FLOOR, FLOOR + STANDOFF_H, STANDOFF_BASE_D, STANDOFF_TOP_D)
        # Its foot is joined to the long wall beside it, where the gap between them is too
        # thin to print; its top stays a plain ring, which prints as walls alone.
        wall_y = (Y0 + WALL, y) if y < PCB_Y / 2 else (y, Y1 - WALL)
        half = STANDOFF_TOP_D / 2
        body += box(x - half, x + half, *wall_y, FLOOR, FLOOR + STANDOFF_FOOT_H)
    # A cone over each head recess, so its roof prints without a bridge: 50 degrees from
    # horizontal, clear of the 45 degree limit on a meshed file too.
    roof = 1.2 * (HEAD_D - SCREW_CLEAR_D) / 2
    notch_y = (SD_Y - SD_NOTCH_W / 2, SD_Y + SD_NOTCH_W / 2)
    cuts = [box(X0 - THROUGH, SD_NOTCH_REACH, *notch_y, 0, BASE_WALL_TOP)]
    for x, y in HOLES:
        cuts.append(cylinder(x, y, 0, FLOOR + STANDOFF_H, SCREW_CLEAR_D))
        cuts.append(cylinder(x, y, 0, HEAD_DEPTH, HEAD_D))
        mouth = HEAD_D + 2 * HEAD_MOUTH_CHAMFER
        cuts.append(cone(x, y, 0, HEAD_MOUTH_CHAMFER, mouth, HEAD_D))
        cuts.append(cone(x, y, HEAD_DEPTH, HEAD_DEPTH + roof, HEAD_D, SCREW_CLEAR_D))
    body -= cuts
    assert abs(body.bounding_box().size.Z - BASE_WALL_TOP) < 1e-6, body.bounding_box().size
    return body


def lid_body():
    """The lid where it sits on the assembled housing."""
    body = plate(LID_RIM, HEIGHT) - plate(LID_RIM, CEILING, inset=WALL)
    body = bd.chamfer(body.edges().group_by(bd.Axis.Z)[-1], EDGE_CHAMFER)
    for x, y in HOLES:
        body += cylinder(x, y, PCB_TOP, CEILING, POST_D)
        # A web from each post to the long wall beside it, clear of the board's parts.
        wall_y = (Y0 + WALL, y) if y < PCB_Y / 2 else (y, Y1 - WALL)
        body += box(x - POST_WEB / 2, x + POST_WEB / 2, *wall_y, PCB_TOP + 2.0, CEILING)
    cuts = []
    for x, y in HOLES:
        cuts.append(cylinder(x, y, PCB_TOP, PCB_TOP + SCREW_PILOT_DEPTH, SCREW_PILOT_D))
    for x0, x1, height in SIDE_PORTS:
        cuts.append(box(x0, x1, Y0 - THROUGH, Y0 + WALL + THROUGH, LID_RIM, PCB_TOP + height))
    for y0, y1, height in END_PORTS:
        cuts.append(box(X1 - WALL - THROUGH, X1 + THROUGH, y0, y1, LID_RIM, PCB_TOP + height))
    for y in END_VENTS:
        slot = (y - END_VENT_W / 2, y + END_VENT_W / 2)
        cuts.append(box(X0 - THROUGH, X0 + WALL + THROUGH, *slot, LID_RIM, PCB_TOP + END_VENT_H))
    for x in TOP_VENTS:
        cuts.append(
            box(
                x - TOP_VENT_W / 2,
                x + TOP_VENT_W / 2,
                TOP_VENT_Y - TOP_VENT_L / 2,
                TOP_VENT_Y + TOP_VENT_L / 2,
                CEILING,
                HEIGHT,
            )
        )
    body -= cuts
    return body


def turned_over(shape):
    """A shape turned over about x between top-up and top-down (its own inverse)."""
    return bd.Pos(*LID_TURN) * bd.Rot(180, 0, 0) * shape


def as_printed(lid_shape):
    return bd.Pos(0, PLATE_PITCH, 0) * turned_over(lid_shape)


def as_assembled(lid_shape):
    return turned_over(bd.Pos(0, -PLATE_PITCH, 0) * lid_shape)


@model
def base():
    return finish(base_body(), "base", "#3a3f47")


@model
def lid():
    body = as_printed(lid_body())
    size = body.bounding_box().size
    assert abs(size.Z - (HEIGHT - LID_RIM)) < 1e-6 and abs(body.bounding_box().min.Z) < 1e-6, size
    return finish(body, "lid", "#c8402c")


@model
def housing():
    bottom = base().shape
    top = as_assembled(lid().shape)
    top.label = "lid"
    return bd.Compound(children=[bottom, top], label="pi4_housing")


if __name__ == "__main__":
    housing()
