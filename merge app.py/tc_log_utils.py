#!/usr/bin/env python
# TC validation log utilities — Python 2.6+ compatible.
#
# Standard line format (same as other TC monitoring logs):
#   CHECK TITLE|description|OK
#   CHECK TITLE|description|not_ok
#
# Convert legacy Outlook .msg to .log:
#   python tc_log_utils.py
#   python tc_log_utils.py --dry-run
#
# Write a log from a production script (Python 2.6):
#   from tc_log_utils import log_line, write_log_file
#   write_log_file("/path/to/report.log", [
#       log_line("THR_CTRL", "all threads up", ok=True),
#       log_line("ERROR_FILE", "no records", ok=True),
#   ])

from __future__ import print_function
from __future__ import with_statement

import codecs
import os
import re
import sys

OLE_MAGIC = "\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

FOLDER_CHECK = {
    "ac1_control_problematic_files": "AC1_Control_Problematic_Files",
    "tc_health_check": "TC_Health_Check",
    "rerate_backlog": "Rerate_Backlog_Status",
    "reject_status": "TC_Bill_Reject_Status",
    "process_crash": "TC_Process_Crash",
    "usage_backlog_alert": "TC_Usage_Backlog_Alert",
    "thread_control_down": "TC_Thread_Control_Down",
    "avm1_es_alerts": "AVM1_ES_Alerts",
}

SECTIONED = frozenset([
    "AC1_Control_Problematic_Files",
    "TC_Health_Check",
    "Rerate_Backlog_Status",
])
ALERTS = frozenset([
    "TC_Process_Crash",
    "TC_Usage_Backlog_Alert",
    "TC_Thread_Control_Down",
    "AVM1_ES_Alerts",
])

_TC_CAUTION_RE = re.compile(
    r"CAUTION\s*:\s*This email is from an external source[^\n]*\n?", re.I)
_TC_FOOTER_RE = re.compile(r"\n-\s*Tasker\s*$", re.I)
_SUBJECT_LINE_RE = re.compile(r"^SUBJECT:.*$", re.I | re.M)


# ---------------------------------------------------------------------------
# Public API for production scripts (Python 2.6)
# ---------------------------------------------------------------------------

def log_line(title, detail, ok=True):
    """Build one pipe-delimited validation log line."""
    status = u"OK" if ok else u"not_ok"
    title = (title or u"REPORT").replace(u"|", u"/").strip()
    detail = (detail or u"").replace(u"|", u"/").strip()
    return u"{0}|{1}|{2}".format(title, detail, status)


def write_log_file(path, lines):
    """Write validation log lines to *path* (creates parent dirs)."""
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    text = u"\n".join(lines) + u"\n"
    with codecs.open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def validation_base():
    root = os.environ.get(
        "VALIDATION_HOME", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "validation", "TC")


# ---------------------------------------------------------------------------
# .msg reading (olefile optional)
# ---------------------------------------------------------------------------

def _decode_msg_stream(ole, path, upper_leaf):
    try:
        raw = ole.openstream(path).read()
    except Exception:
        return u""
    if upper_leaf.endswith("001F"):
        return raw.decode("utf-16-le", "ignore")
    return raw.decode("cp1252", "ignore")


def _scrape_msg_bytes(filepath):
    try:
        with open(filepath, "rb") as fh:
            raw = fh.read()
    except Exception:
        return u""
    try:
        text = raw.decode("utf-16-le", "ignore")
    except Exception:
        text = raw.decode("cp1252", "ignore")
    return re.sub(r"[^\t\r\n\x20-\x7e]", "", text)


def read_msg_text(filepath):
    try:
        import olefile
        ole = olefile.OleFileIO(filepath)
    except Exception:
        return _scrape_msg_bytes(filepath)
    subject, body = u"", u""
    attachments = {}
    try:
        for entry in ole.listdir(streams=True, storages=False):
            leaf = entry[-1]
            up = leaf.upper()
            if up.startswith("__SUBSTG1.0_0037"):
                subject = subject or _decode_msg_stream(ole, entry, up)
            elif up.startswith("__SUBSTG1.0_1000"):
                val = _decode_msg_stream(ole, entry, up)
                if val and len(val) > len(body):
                    body = val
            elif up.startswith("__SUBSTG1.0_3707") or up.startswith("__SUBSTG1.0_3704"):
                store = entry[0]
                attachments.setdefault(store, {})["name"] = _decode_msg_stream(
                    ole, entry, up)
            elif up.startswith("__SUBSTG1.0_3701"):
                store = entry[0]
                try:
                    attachments.setdefault(store, {})["data"] = ole.openstream(
                        entry).read()
                except Exception:
                    pass
    finally:
        try:
            ole.close()
        except Exception:
            pass
    parts = []
    if subject:
        parts.append("SUBJECT: " + subject.strip())
    if body:
        parts.append(body)
    for info in attachments.values():
        name = (info.get("name") or u"").strip()
        data = info.get("data")
        if data and name.lower().endswith((".csv", ".txt", ".log", ".dat")):
            try:
                parts.append(u"[ATTACHMENT: {0}]\n{1}".format(
                    name, data.decode("utf-8", "ignore")))
            except Exception:
                pass
    return u"\n\n".join(p for p in parts if p)


def load_report_text(filepath):
    low = filepath.lower()
    if low.endswith(".msg") or _is_ole(filepath):
        return read_msg_text(filepath)
    try:
        with codecs.open(filepath, "r", encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except Exception:
        with open(filepath, "r") as fh:
            text = fh.read()
    if text and "\x00" in text[:500]:
        return read_msg_text(filepath)
    return text


def _is_ole(path):
    try:
        with open(path, "rb") as fh:
            return fh.read(8) == OLE_MAGIC
    except Exception:
        return False


def _needs_convert(path):
    low = path.lower()
    if low.endswith(".msg"):
        return True
    if low.endswith(".log") and _is_ole(path):
        return True
    if low.endswith(".log"):
        try:
            with open(path, "rb") as fh:
                chunk = fh.read(4096)
            if "\x00" in chunk[:200]:
                return True
        except Exception:
            pass
    return False


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _strip_html(text):
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</tr>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    return re.sub(r"[ \t]+", " ", text)


def _tc_prep_text(content):
    content = content or u""
    if isinstance(content, str):
        try:
            content = content.decode("utf-8", "ignore")
        except Exception:
            pass
    sample = content[:2000].lower()
    if "<html" in sample or "<table" in sample:
        content = _strip_html(content)
    content = _TC_CAUTION_RE.sub("", content)
    content = _TC_FOOTER_RE.sub("", content)
    return content


def _tc_split_cells(line):
    cells = [c.strip() for c in line.split("\t")]
    while cells and cells[-1] == "":
        cells.pop()
    return cells


def _tc_subject(text):
    m = re.search(r"^SUBJECT:\s*(.+)$", text, re.I | re.M)
    return m.group(1).strip() if m else ""


def _section_col(section, col_name):
    headers = [h.upper() for h in section.get("headers", [])]
    try:
        return headers.index(col_name.upper())
    except ValueError:
        return -1


def _bdn_int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def _tc_human_num(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    if n >= 1000000:
        return "{0:.1f}M".format(n / 1000000.0)
    if n >= 1000:
        return "{0:.1f}K".format(n / 1000.0)
    return str(n)


def _finalize_sectioned(name, data):
    sections = data["sections"]
    status = "PASS"
    bad_titles = set()
    if name == "AC1_Control_Problematic_Files":
        skip = set(["START_DATE", "END_DATE"])
        for sec in sections:
            title = (sec.get("title") or "").upper()
            if title in skip:
                continue
            rows = sec.get("rows", [])
            if not rows:
                continue
            idx = _section_col(sec, "FILE_COUNT")
            if idx >= 0:
                bad_titles.add(sec.get("title"))
            else:
                bad_titles.add(sec.get("title"))
        status = "FAIL" if bad_titles else "PASS"
    elif name == "TC_Health_Check":
        for sec in sections:
            title = (sec.get("title") or "").upper()
            rows = sec.get("rows", [])
            if title == "THR_CTRL":
                sidx = _section_col(sec, "THREAD_STATUS")
                down = [r for r in rows if sidx >= 0 and sidx < len(r)
                        and r[sidx].upper() == "DN"]
                if down:
                    bad_titles.add(sec.get("title"))
            elif title == "ERROR_FILE" and rows:
                bad_titles.add(sec.get("title"))
            elif title == "TRB1_SUB_ERRS" and rows:
                bad_titles.add(sec.get("title"))
        status = "FAIL" if bad_titles else "PASS"
    elif name == "Rerate_Backlog_Status":
        status = "PASS"
    data["status"] = status
    data["bad_titles"] = bad_titles


def parse_sectioned_report(content, name):
    text = _tc_prep_text(content)
    subject = _tc_subject(text)
    body = _SUBJECT_LINE_RE.sub("", text)
    blocks = re.split(r"\n\s*_{3,}\s*\n", body)
    sections = []
    for block in blocks:
        lines = [ln.rstrip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        title = u""
        tab_lines = []
        for ln in lines:
            if u"\t" in ln:
                tab_lines.append(ln)
            elif not tab_lines and not title:
                title = ln.strip()
            elif not tab_lines:
                title = (title + u" " + ln.strip()).strip()
        if not tab_lines:
            continue
        headers = _tc_split_cells(tab_lines[0])
        rows = []
        for ln in tab_lines[1:]:
            cells = _tc_split_cells(ln)
            if cells:
                rows.append(cells)
        sections.append({"title": title, "headers": headers, "rows": rows})
    data = {"subject": subject, "sections": sections}
    _finalize_sectioned(name, data)
    return data


def parse_tc_alert(content):
    text = _tc_prep_text(content)
    subject = _tc_subject(text)
    body = _SUBJECT_LINE_RE.sub("", text).strip()
    headline = u""
    for line in body.splitlines():
        line = line.strip().strip("-").strip()
        if line and ":" not in line and not set(line) <= set("-_= "):
            headline = line
            break
    return {
        "subject": subject,
        "headline": headline or subject,
    }


def parse_reject_status(content):
    text = _tc_prep_text(content)
    subject = _tc_subject(text)
    csv_block = text
    m = re.search(r"\[ATTACHMENT:[^\]]*\]\s*\n", text)
    if m:
        csv_block = text[m.end():]
    lines = [ln for ln in csv_block.splitlines() if ln.strip()]
    header = []
    data_rows = []
    for ln in lines:
        if "," not in ln:
            continue
        cells = [c.strip() for c in ln.split(",")]
        up = [h.upper() for h in cells]
        if not header and ("BA_NO" in up or "REASON" in up or "RESP_TEAM" in up):
            header = cells
            continue
        if header and len(cells) >= 2:
            data_rows.append(cells)
    total = len(data_rows)
    by_team = {}
    by_reason = {}
    up = [h.upper() for h in header]
    ti = up.index("RESP_TEAM") if "RESP_TEAM" in up else -1
    ri = up.index("REASON") if "REASON" in up else -1
    for r in data_rows:
        if ti >= 0 and ti < len(r):
            by_team[r[ti]] = by_team.get(r[ti], 0) + 1
        if ri >= 0 and ri < len(r):
            key = r[ri][:60]
            by_reason[key] = by_reason.get(key, 0) + 1
    top_reasons = sorted(by_reason.items(), key=lambda kv: -kv[1])[:10]
    teams = sorted(by_team.items(), key=lambda kv: -kv[1])
    return {
        "subject": subject or "MAIL from REJECT_STATUS",
        "total": total,
        "teams": teams,
        "top_reasons": top_reasons,
    }


# ---------------------------------------------------------------------------
# Convert parsed data -> standard log lines
# ---------------------------------------------------------------------------

def section_to_log_lines(check_name, data):
    lines = []
    bad = data.get("bad_titles") or set()
    skip = set(["START_DATE", "END_DATE"])
    for sec in data.get("sections", []):
        title = (sec.get("title") or check_name).strip()
        if title.upper() in skip:
            continue
        rows = sec.get("rows", [])
        is_bad = title in bad
        if check_name == "Rerate_Backlog_Status":
            if "OVER_ALL_RERATE_BACKLOG" in title.upper():
                idx = _section_col(sec, "OVER_ALL_BACKLOG")
                total = 0
                for row in rows:
                    if idx >= 0 and idx < len(row):
                        total += _bdn_int(re.sub(r"[^\d]", "", row[idx]) or 0)
                detail = "overall backlog {0}".format(_tc_human_num(total))
                lines.append(log_line(title, detail, ok=True))
            else:
                detail = "{0} row(s)".format(len(rows)) if rows else "no records"
                lines.append(log_line(title, detail, ok=not is_bad))
        elif check_name == "AC1_Control_Problematic_Files":
            idx = _section_col(sec, "FILE_COUNT")
            if idx >= 0 and rows:
                count = sum(_bdn_int(re.sub(r"[^\d]", "", row[idx]) or 0)
                            for row in rows if idx < len(row))
                detail = "problematic file count {0}".format(count)
            else:
                detail = ("{0} problematic row(s)".format(len(rows))
                          if rows else "no records")
            lines.append(log_line(title, detail, ok=not is_bad))
        elif check_name == "TC_Health_Check":
            if title.upper() == "THR_CTRL":
                sidx = _section_col(sec, "THREAD_STATUS")
                down = [r for r in rows if sidx >= 0 and sidx < len(r)
                        and r[sidx].upper() == "DN"]
                detail = ("{0} thread(s) down".format(len(down)) if down
                          else "all threads up")
                lines.append(log_line(title, detail, ok=not down))
            elif rows:
                detail = "{0} issue row(s)".format(len(rows))
                lines.append(log_line(title, detail, ok=not is_bad))
            else:
                lines.append(log_line(title, "no records", ok=True))
        else:
            detail = "{0} row(s)".format(len(rows)) if rows else "no records"
            lines.append(log_line(title, detail, ok=not is_bad))
    if not lines:
        ok = data.get("status") != "FAIL"
        subj = (data.get("subject") or check_name)[:120]
        lines.append(log_line(check_name, subj, ok=ok))
    return lines


def reject_to_log_lines(data):
    lines = [log_line("BILL REJECT STATUS",
                      "total rejects {0}".format(data.get("total", 0)),
                      ok=True)]
    for team, count in data.get("teams", [])[:10]:
        lines.append(log_line("BILL REJECT TEAM",
                              "{0} - count {1}".format(team or "unknown", count),
                              ok=True))
    for reason, count in data.get("top_reasons", [])[:5]:
        lines.append(log_line("BILL REJECT REASON",
                              "{0} - count {1}".format(reason, count),
                              ok=True))
    return lines


def alert_to_log_lines(check_name, data):
    headline = data.get("headline") or data.get("subject") or "alert received"
    title = check_name.replace("_", " ").upper()
    return [log_line(title, headline[:200], ok=False)]


def convert_file(path, check_name, dry_run=False):
    content = load_report_text(path)
    if check_name in SECTIONED:
        lines = section_to_log_lines(
            check_name, parse_sectioned_report(content, check_name))
    elif check_name == "TC_Bill_Reject_Status":
        lines = reject_to_log_lines(parse_reject_status(content))
    elif check_name in ALERTS:
        lines = alert_to_log_lines(check_name, parse_tc_alert(content))
    else:
        return None

    dest = path[:-4] + ".log" if path.lower().endswith(".msg") else path
    text = u"\n".join(lines) + u"\n"
    if dry_run:
        return dest, text
    write_log_file(dest, lines)
    if path != dest and os.path.isfile(path):
        os.remove(path)
    return dest, text


def convert_all(base, dry_run=False):
    converted = []
    for dirname, check_name in FOLDER_CHECK.items():
        folder = os.path.join(base, dirname)
        if not os.path.isdir(folder):
            continue
        for root, _dirs, files in os.walk(folder):
            for name in files:
                low = name.lower()
                if not (low.endswith(".msg") or low.endswith(".log")):
                    continue
                path = os.path.join(root, name)
                if not _needs_convert(path):
                    continue
                result = convert_file(path, check_name, dry_run=dry_run)
                if result:
                    converted.append((path, result[0], result[1]))
    return converted


def main(argv=None):
    dry_run = "--dry-run" in (argv if argv is not None else sys.argv[1:])
    base = validation_base()
    if not os.path.isdir(base):
        print("ERROR: not found: {0}".format(base))
        return 1
    items = convert_all(base, dry_run=dry_run)
    if not items:
        print("No TC .msg / binary .log files need conversion.")
        return 0
    label = "Would write" if dry_run else "Wrote"
    for src, dest, text in items:
        nlines = len(text.strip().splitlines())
        print("{0} {1} -> {2} ({3} lines)".format(label, src, dest, nlines))
        if dry_run:
            print(text[:400])
            if len(text) > 400:
                print("...")
    print("{0} {1} file(s).".format(label, len(items)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
