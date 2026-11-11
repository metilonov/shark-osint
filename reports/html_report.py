from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape

ENV = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                  autoescape=select_autoescape(["html", "xml"]))


def render_html(data: dict) -> bytes:
    return ENV.get_template("report.html").render(report=data).encode("utf-8")
