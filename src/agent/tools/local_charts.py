"""Dependency-free local SVG charts; input text is always XML escaped."""
from html import escape
import math
from agent.artifacts import publish_artifact

LOCAL_TYPES = {"bar", "column", "pie", "line"}
COLORS = ["#208778", "#56a89b", "#9bcabe", "#d5b77a", "#688995", "#a28bab"]


def render_chart(kind, config):
    if kind not in LOCAL_TYPES:
        raise ValueError("Unsupported local chart type")
    data = config.get("data")
    if not isinstance(data, list) or not 1 <= len(data) <= 60:
        raise ValueError("data must contain 1-60 category/value rows; paginate larger datasets")
    if config.get("group") or config.get("stack") or any(isinstance(r, dict) and r.get("group") for r in data):
        raise ValueError("Local charts do not support grouped/stacked data; aggregate first or draw separate charts")
    rows = []
    for row in data:
        if not isinstance(row, dict) or "category" not in row:
            raise ValueError("Each row requires category and numeric value")
        try:
            value = float(row["value"])
        except (KeyError, ValueError, TypeError):
            raise ValueError("Each row requires category and numeric value") from None
        if not math.isfinite(value):
            raise ValueError("Values must be finite")
        rows.append((str(row["category"])[:100], value))
    if kind == "pie" and (any(v < 0 for _, v in rows) or sum(v for _, v in rows) <= 0):
        raise ValueError("Pie charts require non-negative values with a positive total")
    w = 960
    h = max(430, 110 + len(rows)*34) if kind in {"bar", "pie"} else 520
    title = str(config.get("title", "数据统计"))[:150]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" role="img"><title>{escape(title)}</title><rect width="100%" height="100%" rx="16" fill="#f6f8f5"/><g font-family="Microsoft YaHei, Noto Sans CJK SC, sans-serif" fill="#24373c">']
    def text(x, y, value, size=16, anchor="start"):
        out.append(f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" text-anchor="{anchor}">{escape(str(value))}</text>')
    text(32, 40, title, 24)
    if kind == "pie":
        total = sum(v for _, v in rows)
        start = -math.pi/2
        cx, cy, radius = 245, 250, 150
        for i, (label, value) in enumerate(rows):
            angle = value/total*2*math.pi
            end = start+angle
            color = COLORS[i % len(COLORS)]
            if value == total:
                out.append(f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="{color}"/>')
            elif value > 0:
                x1,y1=cx+radius*math.cos(start),cy+radius*math.sin(start)
                x2,y2=cx+radius*math.cos(end),cy+radius*math.sin(end)
                out.append(f'<path d="M {cx},{cy} L {x1},{y1} A {radius},{radius} 0 {int(angle>math.pi)},1 {x2},{y2} Z" fill="{color}"/>')
            y=94+i*34
            out.append(f'<rect x="460" y="{y-14}" width="14" height="14" fill="{color}"/>')
            text(485,y,f'{label[:22]}  {value:g} ({value/total:.1%})')
            start=end
    else:
        low=min(0,min(v for _,v in rows)); high=max(0,max(v for _,v in rows))
        span=high-low or 1
        if kind == "bar":
            def x(v): return 240+(v-low)/span*610
            zero=x(0)
            out.append(f'<path d="M {zero},65 V {h-25}" stroke="#cad8d3"/>')
            for i,(label,value) in enumerate(rows):
                y=85+i*34
                text(225,y+5,label[:20],15,"end")
                out.append(f'<rect x="{min(zero,x(value))}" y="{y-14}" width="{abs(x(value)-zero)}" height="23" rx="4" fill="{COLORS[i%len(COLORS)]}"/>')
                text(x(value)+(-8 if value<0 else 8),y+4,f'{value:g}',14,"end" if value<0 else "start")
        else:
            left,right,top,bottom=75,910,85,390
            def y(v): return bottom-(v-low)/span*(bottom-top)
            step=(right-left)/len(rows)
            for t in range(5):
                value=low+span*t/4; yy=y(value)
                out.append(f'<path d="M {left},{yy} H {right}" stroke="#dce5df"/>')
                text(left-10,yy+5,f'{value:g}',12,"end")
            points=[]
            for i,(label,value) in enumerate(rows):
                xx=left+step*(i+.5); yy=y(value)
                if kind=="column":
                    out.append(f'<rect x="{xx-step*.3}" y="{min(yy,y(0))}" width="{step*.6}" height="{abs(yy-y(0))}" fill="{COLORS[i%len(COLORS)]}"/>')
                else:
                    points.append(f'{xx},{yy}')
                    out.append(f'<circle cx="{xx}" cy="{yy}" r="4" fill="#208778"/>')
                text(xx,yy-8,f'{value:g}',12,"middle")
                out.append(f'<text transform="translate({xx},410) rotate(35)" font-size="12">{escape(label[:12])}</text>')
            if points:
                out.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="#208778" stroke-width="3"/>')
    out.append('</g></svg>')
    return ''.join(out)


def generate_local_chart(kind, config):
    svg = render_chart(kind, config)
    url = publish_artifact(svg.encode("utf-8"), ".svg")
    return f"Local chart generated. Include this image in the final answer: ![统计图]({url})"
