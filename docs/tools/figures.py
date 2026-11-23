"""Animated figures of the Concepts pages, drawn at build time.

A page asks for a figure with a line of its own::

    <!-- figure: continuous-balanced -->

and this MkDocs hook replaces it with an inline SVG and its step controls.
Nothing generated is versioned: what is versioned is the description of each
figure below, so changing a number on a figure is a one-line change, and the
numbers themselves are pinned by ``tests/test_docs_concepts_001.py``.

How a figure moves. Every drawn element may carry ``data-from`` and
``data-until``; ``docs/manual/assets/javascripts/stepper.js`` shows it while
the current step lies in that range. A value token rides along its arrow
(``<animateMotion>``) when its step starts. Step 0 is the setup; the last step
of a propagation figure is the settled regime, where the passes give way to
the rates actually exchanged.

Channels are told apart by dash pattern as well as by colour, so a figure
survives black and white: the offer is dotted, the demand dashed, the
production solid, a release dash-dotted.
"""

import html
import math
import re

#: A marker may be indented, inside a content tab; the figure keeps the indent.
FIGURE_MARKER = re.compile(r"^([ \t]*)<!-- figure: ([a-z0-9-]+) -->$", re.M)

#: Arrow direction of each channel: True travels with the flow (source to
#: consumer), False against it.
DOWNSTREAM = {"offer": True, "demand": False, "prod": True, "release": False}

#: Vertical offset of each pass relative to the connection's centre line, so
#: the three passes of one connection sit side by side.
PASS_OFFSET = {"offer": -24, "demand": 0, "prod": 24, "release": 0}

CHANNEL_NAME = {
    "offer": "offer",
    "demand": "demand",
    "prod": "production",
    "release": "release",
}


def esc(text):
    return html.escape(str(text), quote=True)


def fmt(value):
    """A rate as the figures print it: no trailing zero."""
    if isinstance(value, str):
        return value
    return f"{value:g}"


def steps_attr(start, until=None):
    attr = f' data-from="{start}"'
    if until is not None:
        attr += f' data-until="{until}"'
    return attr


class Figure:
    """One SVG, its steps and their captions."""

    def __init__(self, fid, width, height, captions, label):
        self.fid = fid
        self.width = width
        self.height = height
        self.captions = captions
        self.label = label
        self.layers = {"back": [], "mid": [], "front": []}

    @property
    def last_step(self):
        return len(self.captions) - 1

    def add(self, svg, layer="mid"):
        self.layers[layer].append(svg)

    # -- primitives --------------------------------------------------------

    def box(self, x, y, w, h, title, badge=None, badge_channel=None, kind="comp",
            start=0, until=None):
        cx = x + w / 2
        has_badge = badge is not None
        ty = y + h / 2 + (-12 if has_badge else 9)
        out = [
            f'<g class="mu-box mu-{kind}"{steps_attr(start, until)}>',
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16"/>',
            f'<text class="mu-title" x="{cx}" y="{ty}">{esc(title)}</text>',
        ]
        if has_badge:
            bw = max(110, 13.5 * len(badge) + 28)
            by = y + h / 2 + 6
            klass = f"mu-badge mu-{badge_channel}" if badge_channel else "mu-badge"
            out += [
                f'<g class="{klass}">',
                f'<rect x="{cx - bw / 2}" y="{by}" width="{bw}" height="34" rx="17"/>',
                f'<text x="{cx}" y="{by + 24}">{esc(badge)}</text>',
                "</g>",
            ]
        out.append("</g>")
        self.add("".join(out), "back")

    def text(self, x, y, content, klass="mu-note", anchor="middle", start=0,
             until=None):
        self.add(
            f'<text class="{klass}" x="{x}" y="{y}" text-anchor="{anchor}"'
            f"{steps_attr(start, until)}>{esc(content)}</text>",
            "front",
        )

    def link(self, p0, p1, orient="h", offset=0, reverse=False):
        """Path between two anchors, shifted by ``offset`` across its run."""
        (x0, y0), (x1, y1) = p0, p1
        if orient == "h":
            y0, y1 = y0 + offset, y1 + offset
            mx = (x0 + x1) / 2
            d = f"M{x0} {y0} C{mx} {y0} {mx} {y1} {x1} {y1}"
            rd = f"M{x1} {y1} C{mx} {y1} {mx} {y0} {x0} {y0}"
        else:
            x0, x1 = x0 + offset, x1 + offset
            my = (y0 + y1) / 2
            d = f"M{x0} {y0} C{x0} {my} {x1} {my} {x1} {y1}"
            rd = f"M{x1} {y1} C{x1} {my} {x0} {my} {x0} {y0}"
        return rd if reverse else d

    def arrow(self, d, channel, start, until=None, width=5, token=None):
        """A channel arrow, drawn at ``start``, with a token riding it."""
        mid = f"{self.fid}-{channel}"
        out = [
            f'<g class="mu-pass mu-{channel}"{steps_attr(start, until)}>',
            f'<path class="mu-line" d="{d}" stroke-width="{width}"'
            f' marker-end="url(#{mid})"/>',
            f'<path class="mu-flowing" d="{d}"/>',
        ]
        if token is not None:
            label = fmt(token)
            tw = max(52, 14 * len(label) + 24)
            out += [
                '<g class="mu-token">',
                f'<rect x="{-tw / 2}" y="-19" width="{tw}" height="38" rx="19"/>',
                f'<text x="0" y="8">{esc(label)}</text>',
                f'<animateMotion dur="1.4s" begin="indefinite" fill="freeze"'
                f' keyPoints="0;0.8" keyTimes="0;1" calcMode="linear" path="{d}"/>',
                "</g>",
            ]
        out.append("</g>")
        self.add("".join(out), "mid")

    def settled(self, d, value, at, start, ref=None):
        """The rate a connection settles on.

        With ``ref``, the largest rate of the figure, the stroke grows with
        the value; without it (rates in different units) it is fixed.
        """
        if ref:
            width = 5 + 13 * math.sqrt(max(value, 0.0) / ref)
        else:
            width = 9
        self.arrow(d, "prod", start, width=width)
        label = fmt(value) + " u/t"
        tw = max(70, 14 * len(label) + 26)
        x, y = at
        self.add(
            f'<g class="mu-token mu-prod mu-static"{steps_attr(start)}>'
            f'<rect x="{x - tw / 2}" y="{y - 19}" width="{tw}" height="38" rx="19"/>'
            f'<text x="{x}" y="{y + 8}">{esc(label)}</text></g>',
            "front",
        )

    # -- rendering ---------------------------------------------------------

    def markers(self):
        out = []
        for channel in DOWNSTREAM:
            out.append(
                f'<marker id="{self.fid}-{channel}" class="mu-{channel}"'
                ' viewBox="0 0 10 10" refX="7" refY="5" markerWidth="22"'
                ' markerHeight="22" markerUnits="userSpaceOnUse"'
                ' orient="auto-start-reverse">'
                '<path d="M0 0 L10 5 L0 10 Z"/></marker>'
            )
        return "<defs>" + "".join(out) + "</defs>"

    def render(self):
        steps = self.last_step
        svg = (
            f'<svg class="mu-svg" viewBox="0 0 {self.width} {self.height}"'
            f' role="img" aria-label="{esc(self.label)}">'
            + self.markers()
            + "".join(self.layers["back"])
            + "".join(self.layers["mid"])
            + "".join(self.layers["front"])
            + "</svg>"
        )
        captions = "".join(
            f'<p class="mu-caption"{steps_attr(i, i)}>{esc(text)}</p>'
            for i, text in enumerate(self.captions)
        )
        return (
            f'<figure class="mu-figure" id="fig-{self.fid}" data-steps="{steps}">'
            f'{svg}<div class="mu-captions">{captions}</div></figure>'
        )


# -- continuous propagation ------------------------------------------------

PASS_CAPTIONS = [
    "The offer travels downstream: each producer publishes what it could "
    "deliver if asked without bound.",
    "The demand travels upstream: each consumer asks for what it can use, "
    "knowing what its other inputs can supply.",
    "The production travels downstream: each connection carries the lesser of "
    "what is offered and what is asked.",
]


def propagation(fid, boxes, edges, setup, settled_caption, extra=None,
                height=400, label="Flow propagation"):
    """A figure of the three passes, then the settled regime.

    ``edges`` are ``(p0, p1, passes, settled, label_at)``, ``passes`` mapping a
    channel to the value its token carries. ``extra`` inserts steps between
    the production pass and the settled regime, as ``(caption, [(p0, p1,
    channel, value)])``.
    """
    extra = extra or []
    captions = [setup] + PASS_CAPTIONS + [c for c, _ in extra] + [settled_caption]
    fig = Figure(fid, 1000, height, captions, label)
    last = len(captions) - 1
    for box in boxes:
        fig.box(**box)
    ref = max((e[3] for e in edges if e[3] is not None), default=None)
    for p0, p1, passes, value, at in edges:
        for step, channel in enumerate(("offer", "demand", "prod"), start=1):
            if channel not in passes:
                continue
            d = fig.link(p0, p1, offset=PASS_OFFSET[channel],
                         reverse=not DOWNSTREAM[channel])
            # The offer and the demand give way to an inserted step; the
            # production stays until the regime settles.
            until = last - 1 if channel == "prod" else 3
            fig.arrow(d, channel, step, until, token=passes[channel])
        if value is not None:
            fig.settled(fig.link(p0, p1), value, at, last, ref=ref)
    for step, (_, moves) in enumerate(extra, start=4):
        for p0, p1, channel, value in moves:
            d = fig.link(p0, p1, offset=-PASS_OFFSET["prod"] - 24,
                         reverse=not DOWNSTREAM[channel])
            fig.arrow(d, channel, step, last - 1, token=value)
    return fig


def one_to_one(fid, offer, demand, result, conclusion):
    return propagation(
        fid,
        boxes=[
            dict(x=40, y=130, w=230, h=140, title="Source",
                 badge=f"offer {fmt(offer)}", badge_channel="offer"),
            dict(x=730, y=130, w=230, h=140, title="Consumer",
                 badge=f"demand {fmt(demand)}", badge_channel="demand"),
        ],
        edges=[((270, 200), (730, 200),
                {"offer": offer, "demand": demand, "prod": result}, result,
                (500, 200))],
        setup=f"A source able to deliver {fmt(offer)} units per unit of time "
        f"feeds a consumer asking for {fmt(demand)}.",
        settled_caption=conclusion,
        label=f"A source offering {fmt(offer)} feeds a consumer asking "
        f"{fmt(demand)}; {fmt(result)} flows",
    )


def two_consumers_shortage():
    return propagation(
        "continuous-shortage",
        boxes=[
            dict(x=40, y=170, w=230, h=140, title="Source",
                 badge="offer 12", badge_channel="offer"),
            dict(x=730, y=30, w=230, h=140, title="Consumer 1",
                 badge="demand 6", badge_channel="demand"),
            dict(x=730, y=310, w=230, h=140, title="Consumer 2",
                 badge="demand 12", badge_channel="demand"),
        ],
        edges=[
            ((270, 220), (730, 100), {"offer": 12, "demand": 6, "prod": 4}, 4,
             (500, 140)),
            ((270, 260), (730, 380), {"offer": 12, "demand": 12, "prod": 8}, 8,
             (500, 340)),
        ],
        height=480,
        setup="A source of 12 feeds two consumers asking for 6 and 12: 18 "
        "asked, 12 available.",
        settled_caption="The shortage is shared in proportion to the demands: "
        "6/18 and 12/18 of 12, that is 4 and 8. Each consumer gets two thirds "
        "of its need.",
        label="A source of 12 split between demands of 6 and 12 as 4 and 8",
    )


def limited_consumer():
    return propagation(
        "continuous-offer-first",
        boxes=[
            dict(x=40, y=170, w=230, h=140, title="Source",
                 badge="offer 12", badge_channel="offer"),
            dict(x=730, y=30, w=230, h=140, title="Machine",
                 badge="other input 2", badge_channel="offer"),
            dict(x=730, y=310, w=230, h=140, title="Consumer",
                 badge="demand 10", badge_channel="demand"),
        ],
        edges=[
            ((270, 220), (730, 100), {"offer": 12, "demand": 2, "prod": 2}, 2,
             (500, 140)),
            ((270, 260), (730, 380), {"offer": 12, "demand": 10, "prod": 10},
             10, (500, 340)),
        ],
        height=480,
        setup="A source of 12 feeds a machine and a consumer asking for 10. "
        "The machine draws one unit from each of its two inputs, and its other "
        "input can only supply 2.",
        settled_caption="Since the machine knows the offer of its other "
        "input, it asks for 2 rather than its nominal share, and the consumer "
        "gets its whole 10.",
        label="A machine limited by its other input asks for 2, leaving 10 to "
        "its neighbour",
    )


def two_sources():
    return propagation(
        "continuous-two-sources",
        boxes=[
            dict(x=40, y=30, w=230, h=140, title="Source 1",
                 badge="offer 9", badge_channel="offer"),
            dict(x=40, y=310, w=230, h=140, title="Source 2",
                 badge="offer 3", badge_channel="offer"),
            dict(x=730, y=170, w=230, h=140, title="Machine",
                 badge="needs 6", badge_channel="demand"),
        ],
        edges=[
            ((270, 100), (730, 220), {"offer": 9, "demand": 6, "prod": 6}, 4,
             (500, 140)),
            ((270, 380), (730, 260), {"offer": 3, "demand": 6, "prod": 3}, 2,
             (500, 340)),
        ],
        extra=[(
            "The machine receives 9 for a need of 6. It takes what its rule "
            "uses and hands the surplus back in proportion to what each source "
            "delivered: 2 to the first, 1 to the second.",
            [((270, 100), (730, 220), "release", 2),
             ((270, 380), (730, 260), "release", 1)],
        )],
        height=480,
        setup="Two sources offering 9 and 3 feed a machine needing 6. The "
        "demand is not split between them: each is asked for the whole 6.",
        settled_caption="The sources deliver 4 and 2, in proportion to their "
        "offers, and the machine receives exactly what it uses.",
        label="Two sources of 9 and 3 feed a machine needing 6; they deliver "
        "4 and 2",
    )


# -- transformation --------------------------------------------------------


def electrolyser(fid, power):
    scale = min(20.0, power / 50.0)
    water, used, h2, o2 = 9 * scale, 50 * scale, scale, 8 * scale
    limited = power / 50.0 < 20.0
    captions = [
        "An electrolyser carries one rule: 9 of water and 50 of power make 1 "
        "of hydrogen and 8 of oxygen. The rule fixes proportions, not rates.",
        "The demand arrives: 20 of hydrogen asked downstream.",
        f"The offers arrive: plenty of water, {fmt(power)} of power.",
        (f"The scarcest factor sets the scale: min(20 / 1, {fmt(power)} / 50) "
         f"= {fmt(scale)}." + (" Power is short." if limited else
                                " The demand decides.")),
        f"Every flow follows in the declared proportions: {fmt(water)} of "
        f"water and {fmt(used)} of power drawn, {fmt(h2)} of hydrogen and "
        f"{fmt(o2)} of oxygen made. What is not transformed is not drawn.",
    ]
    fig = Figure(fid, 1000, 500, captions,
                 f"An electrolyser rule at scale {fmt(scale)}")
    fig.box(x=40, y=235, w=200, h=110, title="Water", badge="offer 1000",
            badge_channel="offer")
    fig.box(x=400, y=10, w=200, h=110, title="Grid",
            badge=f"offer {fmt(power)}", badge_channel="offer")
    fig.box(x=380, y=210, w=240, h=160, title="Electrolyser", kind="machine")
    fig.box(x=770, y=150, w=200, h=110, title="Hydrogen use",
            badge="demand 20", badge_channel="demand")
    fig.box(x=770, y=330, w=200, h=110, title="Oxygen vent", kind="sink")
    # The rule's coefficients, written on the ports.
    for x, y, t, anchor in ((372, 330, "9", "end"), (522, 200, "50", "start"),
                            (628, 262, "1", "start"), (628, 352, "8", "start")):
        fig.text(x, y, t, klass="mu-coef", anchor=anchor)
    fig.text(500, 345, f"scale {fmt(scale)}", klass="mu-scale", start=3)

    water_d = fig.link((240, 290), (380, 290))
    power_d = fig.link((500, 120), (500, 210), orient="v")
    h2_d = fig.link((620, 270), (770, 205))
    o2_d = fig.link((620, 320), (770, 385))
    fig.arrow(fig.link((620, 270), (770, 205), reverse=True), "demand", 1, 3,
              token=20)
    fig.arrow(fig.link((240, 290), (380, 290), offset=-20), "offer", 2, 3,
              token=1000)
    fig.arrow(fig.link((500, 120), (500, 210), orient="v", offset=-20),
              "offer", 2, 3, token=power)
    for d, value, at in ((water_d, water, (310, 255)),
                         (power_d, used, (560, 165)),
                         (h2_d, h2, (700, 205)),
                         (o2_d, o2, (700, 380))):
        fig.settled(d, value, at, 4)
    return fig


# -- capacities ------------------------------------------------------------


def capacity(fid, state, offer, demand, level, ask, inflow, outflow,
             conclusion):
    captions = [
        f"A volume between a source of {fmt(offer)} and a consumer asking "
        f"{fmt(demand)}. It is {state}, and claims up to 10 for itself while "
        "it has room.",
        f"The consumer's demand, {fmt(demand)}, reaches the volume.",
        (f"The volume asks its source for {fmt(ask)}: "
         + ("its own fill claim, which already covers what passes through."
            if ask == 10 else
            "no room is left, so the demand only passes through.")),
        f"The source answers {fmt(inflow)}.",
        conclusion,
    ]
    fig = Figure(fid, 1000, 420, captions, f"A {state} capacity")
    fig.box(x=30, y=150, w=210, h=130, title="Source",
            badge=f"offer {fmt(offer)}", badge_channel="offer")
    fig.box(x=760, y=150, w=210, h=130, title="Consumer",
            badge=f"demand {fmt(demand)}", badge_channel="demand")
    # The vessel and its level.
    fig.add(
        '<g class="mu-tank">'
        '<rect class="mu-vessel" x="400" y="60" width="200" height="300" rx="28"/>'
        '<clipPath id="{0}-clip"><rect x="404" y="64" width="192" height="292"'
        ' rx="24"/></clipPath>'
        '<rect class="mu-level mu-level-{1}" clip-path="url(#{0}-clip)" x="404"'
        ' y="64" width="192" height="292" style="--lvl:{2}"/>'
        '<text class="mu-title mu-on-light" x="500" y="40">Capacity</text>'
        "</g>".format(fid, state, level),
        "back",
    )
    fig.text(500, 395, {"empty": "empty", "partial": "partial: rising by 7 per "
                        "unit of time", "full": "full"}[state],
             klass="mu-note", start=4)
    fig.arrow(fig.link((760, 215), (600, 215), offset=0), "demand", 1, 3,
              token=demand)
    fig.arrow(fig.link((400, 215), (240, 215)), "demand", 2, 3, token=ask)
    fig.arrow(fig.link((240, 240), (400, 240)), "prod", 3, 3, token=inflow)
    fig.settled(fig.link((240, 215), (400, 215)), inflow, (320, 170), 4,
                ref=10)
    fig.settled(fig.link((600, 215), (760, 215)), outflow, (680, 170), 4,
                ref=10)
    return fig


# -- discrete flows --------------------------------------------------------


def discrete_or():
    captions = [
        "Nominal: the source feeds two redundant blocks, and the target "
        "combines them with an OR logic.",
        "Block 1 fails: its output turns false. The target is still fed "
        "through block 2.",
        "Block 2 fails as well: no input is true, the target is no longer fed.",
        "Block 1 is repaired: its output is true again, and so is the target.",
    ]
    fig = Figure("discrete-or", 1000, 420, captions,
                 "A boolean signal through two redundant blocks")
    fig.box(x=30, y=150, w=200, h=120, title="Source")
    fig.box(x=400, y=20, w=200, h=120, title="Block 1")
    fig.box(x=400, y=280, w=200, h=120, title="Block 2")
    # The target is fed at every step but the third.
    for start, until, fed in ((0, 1, True), (2, 2, False), (3, None, True)):
        fig.box(x=770, y=150, w=200, h=120, title="Target",
                badge="fed" if fed else "not fed",
                kind="discrete-on" if fed else "discrete-off",
                start=start, until=until)

    def signal(p0, p1, start, until, on):
        d = fig.link(p0, p1)
        klass = "mu-signal-on" if on else "mu-signal-off"
        fig.add(
            f'<g class="mu-pass {klass}"{steps_attr(start, until)}>'
            f'<path class="mu-line" d="{d}"/>'
            f'<path class="mu-flowing" d="{d}"/></g>'
        )
        x = (p0[0] + p1[0]) / 2
        y = (p0[1] + p1[1]) / 2 - 14
        fig.text(x, y, "true" if on else "false", klass=f"mu-bool {klass}",
                 start=start, until=until)

    signal((230, 190), (400, 80), 0, None, True)
    signal((230, 230), (400, 340), 0, None, True)
    signal((600, 80), (770, 190), 0, 0, True)
    signal((600, 80), (770, 190), 1, 2, False)
    signal((600, 80), (770, 190), 3, None, True)
    signal((600, 340), (770, 230), 0, 1, True)
    signal((600, 340), (770, 230), 2, None, False)
    for x, y, start, until in ((500, 80, 1, 2), (500, 340, 2, None)):
        fig.add(
            f'<g class="mu-fail"{steps_attr(start, until)}>'
            f'<path d="M{x + 70} {y - 70} l36 36 m0 -36 l-36 36"/></g>',
            "front",
        )
    fig.text(870, 300, "logic: or", klass="mu-note")
    return fig


# -- automata --------------------------------------------------------------


def threshold_automaton():
    captions = [
        "A two-state automaton commanding an order from a measurement, with "
        "an activation threshold at 80 and a release threshold at 40.",
        "The measurement rises but stays under 80: the automaton stays off.",
        "The measurement crosses 80: the transition fires at the crossing, "
        "dated exactly, and the order switches on.",
        "The measurement falls back under 80 but stays above 40: nothing "
        "happens. The gap between the two thresholds is the hysteresis.",
        "The measurement crosses 40: the automaton switches back off.",
    ]
    fig = Figure("automaton-thresholds", 1000, 470, captions,
                 "A threshold automaton with hysteresis")
    # Chart frame: time on x from 60 to 600, value on y (0 at 380, 100 at 60).
    def vy(v):
        return 380 - 3.2 * v

    fig.add(
        '<g class="mu-chart">'
        '<path class="mu-axis" d="M60 40 V380 H610"/>'
        f'<path class="mu-thr" d="M60 {vy(80)} H610"/>'
        f'<path class="mu-thr" d="M60 {vy(40)} H610"/>'
        '<text class="mu-note" x="70" y="30" text-anchor="start">measurement</text>'
        f'<text class="mu-coef" x="615" y="{vy(80) + 7}" text-anchor="start">80</text>'
        f'<text class="mu-coef" x="615" y="{vy(40) + 7}" text-anchor="start">40</text>'
        '<text class="mu-note" x="600" y="410" text-anchor="end">time</text>'
        "</g>",
        "back",
    )
    # The measurement curve in four segments, one per step.
    pts = [(60, 20), (200, 70), (260, 80), (340, 100), (420, 60), (500, 45),
           (540, 40), (600, 25)]
    segments = [(1, pts[0:3]), (2, pts[2:4]), (3, pts[3:6]), (4, pts[5:8])]
    for step, seg in segments:
        d = "M" + " L".join(f"{x} {vy(v)}" for x, v in seg)
        fig.add(f'<path class="mu-curve"{steps_attr(step)} d="{d}"/>')
    # The order, a square wave under the chart.
    fig.add('<text class="mu-note" x="70" y="445" text-anchor="start">order</text>',
            "back")
    order = [(1, "M60 455 H260"), (2, "M260 455 V425 H340"),
             (3, "M340 425 H540"), (4, "M540 425 V455 H600")]
    for step, d in order:
        fig.add(f'<path class="mu-order"{steps_attr(step)} d="{d}"/>')
    # The automaton itself.
    for name, cx, on_steps in (("off", 770, ((0, 1), (4, None))),
                               ("on", 930, ((2, 3),))):
        for start, until in on_steps:
            fig.add(
                f'<g class="mu-state mu-active"{steps_attr(start, until)}>'
                f'<circle cx="{cx}" cy="200" r="58"/></g>',
                "back",
            )
        fig.add(
            f'<g class="mu-state"><circle cx="{cx}" cy="200" r="58"/>'
            f'<text class="mu-title mu-on-light" x="{cx}" y="209">{name}</text></g>'
        )
    fig.add(
        '<g class="mu-trans">'
        '<path d="M800 150 C830 90 870 90 900 150" marker-end="url(#automaton-thresholds-prod)"/>'
        '<path d="M900 250 C870 310 830 310 800 250" marker-end="url(#automaton-thresholds-prod)"/>'
        "</g>"
    )
    fig.text(850, 95, "m ≥ 80", klass="mu-coef")
    fig.text(850, 330, "m ≤ 40", klass="mu-coef")
    return fig


# -- overview --------------------------------------------------------------


def control_loop():
    fig = Figure("control-loop", 1000, 400, ["A model reads as a control loop."],
                 "Control loop: the controller commands the actuator, which acts "
                 "on the process, whose sensor reports a measurement back")
    fig.box(x=350, y=16, w=300, h=90, title="Controller", badge="automaton",
            kind="ctrl")
    fig.box(x=690, y=156, w=300, h=90, title="Actuator",
            badge="production condition")
    fig.box(x=350, y=296, w=300, h=90, title="Process", badge="flows, capacities")
    fig.box(x=10, y=156, w=300, h=90, title="Sensor", badge="thresholds",
            kind="ctrl")
    for d in ("M650 60 H840 V152", "M840 248 V340 H654", "M350 340 H160 V250",
              "M160 156 V60 H346"):
        fig.add(f'<g class="mu-pass mu-prod"><path class="mu-line" d="{d}"'
                ' stroke-width="5" marker-end="url(#control-loop-prod)"/></g>')
    for x, y, t in ((745, 48, "order"), (900, 300, "action"),
                    (255, 330, "quantity"), (255, 48, "measurement")):
        fig.text(x, y, t, klass="mu-note")
    return fig


FIGURES = {
    "control-loop": control_loop,
    "discrete-or": discrete_or,
    "continuous-balanced": lambda: one_to_one(
        "continuous-balanced", 10, 10, 10,
        "Offer and demand balance: 10 units per unit of time flow."),
    "continuous-short": lambda: one_to_one(
        "continuous-short", 10, 15, 10,
        "The offer caps the delivery: 10 flow for 15 asked. The demand is "
        "published as declared, so the shortfall of 5 stays readable."),
    "continuous-demand-led": lambda: one_to_one(
        "continuous-demand-led", 10, 4, 4,
        "The demand drives production: the source delivers 4 of the 10 it "
        "could."),
    "continuous-shortage": two_consumers_shortage,
    "continuous-offer-first": limited_consumer,
    "continuous-two-sources": two_sources,
    "rule-demand-led": lambda: electrolyser("rule-demand-led", 1000.0),
    "rule-input-limited": lambda: electrolyser("rule-input-limited", 500.0),
    "capacity-empty": lambda: capacity(
        "capacity-empty", "empty", 4, 10, 0, 10, 4, 4,
        "With no stock, nothing leaves faster than it arrives: 4 in, 4 out, "
        "and the level stays at zero."),
    "capacity-partial": lambda: capacity(
        "capacity-partial", "partial", 10, 3, 0.5, 10, 10, 3,
        "The volume serves the 3 asked out of its stock and takes the 10 it "
        "claimed: the level rises by 7 per unit of time."),
    "capacity-full": lambda: capacity(
        "capacity-full", "full", 10, 3, 1, 3, 3, 3,
        "Full, the volume only lets through what is asked: 3 in, 3 out, the "
        "source held back to 3."),
    "automaton-thresholds": threshold_automaton,
}


def on_page_markdown(markdown, page, config, files):
    def render(match):
        indent, name = match.groups()
        if name not in FIGURES:
            raise KeyError(f"{page.file.src_path}: unknown figure {name!r}")
        return indent + FIGURES[name]().render()

    return FIGURE_MARKER.sub(render, markdown)
