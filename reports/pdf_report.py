from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

FONT = Path(__file__).resolve().parents[1] / "assets" / "DejaVuSans.ttf"


def render_pdf(data: dict) -> bytes:
    if "SharkFont" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("SharkFont", str(FONT)))
    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        style.fontName = "SharkFont"
        style.wordWrap = "CJK"
    out = BytesIO()
    story = []
    def paragraph(text, style="BodyText"):
        story.append(Paragraph(escape(str(text)), styles[style]))
        story.append(Spacer(1, 7))
    paragraph("SHARK OSINT", "Title")
    paragraph(f"Никнейм: {data['username']} · {data['status']}")
    paragraph(f"{data['started_at']} — {data['finished_at']} · UTC")
    paragraph(f"Время анализа: {data['duration_seconds']:.1f} с")
    paragraph(data["notice"])
    paragraph("Общая статистика", "Heading2")
    labels = {"checked_platforms": "Проверено платформ", "checks": "Проверок инструментами", "profiles": "Профилей", "accessible_pages": "Доступных страниц HTTP 2xx", "uncertain_pages": "Сомнительных страниц", "multi_source_matches": "Совпадений источников"}
    for key, label in labels.items():
        paragraph(f"{label}: {data['statistics'][key]}")
    paragraph("Платформа — уникальный хост без www; покрытие учитывает Sherlock и Maigret. Доступность требует HTTP 2xx именно для URL профиля. Отдельный API не подтверждает доступность страницы. Это не доказывает наличие аккаунта или его владельца.")
    paragraph("Профили, метаданные и источники", "Heading2")
    for profile in data["profiles"]:
        paragraph(profile["platform"], "Heading3")
        paragraph(profile["url"])
        paragraph(f"Уверенность: {profile['confidence']}. Возможное ложное совпадение: да.")
        paragraph(profile["note"])
        for engine, metadata in profile["metadata"].items():
            paragraph(f"Метаданные ({engine}): {metadata}")
        for source in profile["sources"]:
            paragraph(f"{source['engine']} · {source['detected_at']} · HTTP {source['http_status']} · {source['url']}")
            if source.get("checked_url") != source["url"]:
                paragraph(f"Проверенный endpoint: {source.get('checked_url', '')}")
    paragraph("Статусы движков", "Heading2")
    for engine, status in data["engines"].items():
        paragraph(f"{engine}: {status}")
    paragraph("Ошибки проверки", "Heading2")
    for error in data["errors"]:
        paragraph(f"{error['engine']} · {error.get('platform', '')} · {error.get('at', '')}: {error['message']}")
    SimpleDocTemplate(out, title="Shark OSINT", author="Shark Team", pageCompression=1).build(story)
    return out.getvalue()
