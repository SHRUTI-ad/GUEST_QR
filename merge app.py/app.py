import streamlit as st
import streamlit.components.v1 as components
import altair as alt
import pandas as pd
import os
import glob
import json
import quopri
import re
import time
import bisect
from datetime import datetime, timedelta
# ---------------------------------------------------------------------------
# Streamlit compatibility (old VDI / server venv: Streamlit < 1.18, Python 3.6+)
# ---------------------------------------------------------------------------
def _cache_data_compat(**kwargs):
    if hasattr(st, "cache_data"):
        return st.cache_data(**kwargs)
    if hasattr(st, "cache"):
        def decorator(func):
            try:
                return st.cache(func, suppress_st_warning=True)
            except TypeError:
                return st.cache(func)
        return decorator
    def decorator(func):
        return func
    return decorator
 
def _toggle_compat(label, key, help=None, disabled=False, **kwargs):
    if hasattr(st, "toggle"):
        return st.toggle(label, key=key, help=help, disabled=disabled, **kwargs)
    if key not in st.session_state:
        st.session_state[key] = kwargs.get("value", False)
    return st.checkbox(label, key=key, help=help, disabled=disabled)
 
def _datetime_input_compat(label, min_value, max_value, step=None, key=None, disabled=False):
    if hasattr(st, "datetime_input"):
        return st.datetime_input(
            label, min_value=min_value, max_value=max_value,
            step=step, key=key, disabled=disabled,
        )
    current = st.session_state.get(key, max_value)
    if not isinstance(current, datetime):
        current = max_value
    d = st.date_input(
        label + " (date)", value=current.date(),
        min_value=min_value.date(), max_value=max_value.date(),
        key=(key + "_d") if key else None, disabled=disabled,
    )
    t = st.time_input(
        label + " (time)", value=current.time(),
        key=(key + "_t") if key else None, disabled=disabled,
    )
    selected = datetime.combine(d, t)
    if selected < min_value:
        selected = min_value
    if selected > max_value:
        selected = max_value
    if key is not None:
        st.session_state[key] = selected
    return selected
 
def _columns_compat(spec, gap=None):
    if gap is not None:
        try:
            return st.columns(spec, gap=gap)
        except TypeError:
            pass
    return st.columns(spec)
 
def _bordered_container_compat():
    try:
        return st.container(border=True)
    except TypeError:
        return st.container()
 
def _altair_chart_compat(chart):
    try:
        st.altair_chart(chart, use_container_width=True)
    except TypeError:
        st.altair_chart(chart)
 
# --- Report parsers / metric renderers (formerly metric_reports.py) ---
METRIC_REPORT_CHECKS = {
    "BAP_Error_Report",
    "MCO_System_Files_Cleanup",
    "File_System_Usage_Report",
}
JVM_THREADS_CHECKS = {"JVM_Threads_Report"}
BDN_BRN_CHECKS = {"BDN_BRN_Report"}
BAP_COUNT_CHECKS = {"BAP_Error_Report"}
# --- TC reports (log-based; same not_ok keyword as other TC alerts) ------------
TC_SECTIONED_CHECKS = set()
TC_ALERT_CHECKS = set()
TC_REJECT_CHECKS = set()
TC_INFO_CHECKS = set()
TC_REPORT_CHECKS = set()
# --- ABP (.msg based) email alert reports ------------------------------------
# Presence of a fresh alert mail means FAIL (same semantics as TC alert checks).
ABP_EMAIL_ALERT_CHECKS = {
    "CL_Collection_No_Request_CM_to_OMS",
    "CL_BCC_Mercantile_TDX_File_Report",
    "CL_Collection_Activities_Report",
    "CL_Collection_Letters_Monitoring",
    "CL_Health_Check_Report",
    "CL_Collection_Staggering_Backlog",
    "CL_Missing_TDX_Transaction",
    "CL_Receipt_Failure_Monitor",
    "CM_User_Groups_Mismatch",
    "CM_Customer_Type_Mismatch",
    "CM_PCN_BAN_BEN_Status_Mismatch",
    "AR_AC1_Control_Problematic_Files",
    "AR_BL_Mismatch",
    "AR_CL_Bucket_Mismatch",
    "AR3GWLSTNR_Daemon_Failed_File_Urgent",
    "AR3GWLSTR_File_Processing",
    "AR9PYMRCTUPD_Daemon_Processing_Alert",
    "AR_ATB_GL_Recon",
    "AR_Accounts_Stuck_Trial_Period",
    "AR_DD_Validation_Missing_Entries",
    "AR_FAILURE_Entries_Missing",
    "AR_BCC_Missing_Subscription_DD_Rejection",
    "AR_Cost_Center_Change_Report",
    "AR_DSPREJ_Not_Journalized",
    "AR_Feedback_Payment_Files_Pending",
    "AR_IC289_Variance",
    "AR_Nameline1_Null_Name_Data",
    "AR_Missing_Invoices_AR_GL",
    "AR_Subscription_Pay_Means_Mismatch_Backend",
    "AR3GWLSTR_Daemon_Stuck",
    "AR_DDFeedback_Payment_Errored_Files",
    "AR_WriteOff_Exited_Collection_After_Payment",
}
EMAIL_ALERT_CHECKS = ABP_EMAIL_ALERT_CHECKS
JVM_THREAD_HEADERS = (
    "ServerName", "ExecuteThreadIdleCount", "ExecuteThreadTotalCount",
    "StandbyThreadCount", "CompletedRequestCount", "StuckThreadCount",
    "HoggingThreadCount", "QueueLength",
)
_MR_DASHBOARD_NAMES = {
    "BAP_Error_Report": "BAP Error",
    "MCO_System_Files_Cleanup": "MCO Cleanup",
    "File_System_Usage_Report": "FS Usage",
    "JVM_Threads_Report": "JVM Threads",
    "BDN_BRN_Report": "BDN/BRN",
    "AC1_Control_Problematic_Files": "AC1 Prob Files",
    "TC_Health_Check": "TC Health",
    "Rerate_Backlog_Status": "Rerate Backlog",
    "TC_Bill_Reject_Status": "Bill Reject",
    "TC_Process_Crash": "Process Crash",
    "TC_Usage_Backlog_Alert": "Usage Backlog",
    "TC_Thread_Control_Down": "Thread Ctrl Down",
    "AVM1_ES_Alerts": "AVM1 ES",
    "CL_Collection_No_Request_CM_to_OMS": "Coll No CM→OMS",
    "CL_BCC_Mercantile_TDX_File_Report": "BCC TDX",
    "CL_Collection_Activities_Report": "Coll Activities",
    "CL_Collection_Letters_Monitoring": "CL Letters",
    "CL_Health_Check_Report": "CL Health",
    "CL_Collection_Staggering_Backlog": "Stagger Backlog",
    "CL_Missing_TDX_Transaction": "Missing TDX",
    "CL_Receipt_Failure_Monitor": "Receipt Fail",
    "CM_User_Groups_Mismatch": "User Groups",
    "CM_Customer_Type_Mismatch": "Cust Type",
    "CM_PCN_BAN_BEN_Status_Mismatch": "PCN BAN BEN",
    "AR_AC1_Control_Problematic_Files": "AC1 Prob Files",
    "AR_BL_Mismatch": "AR-BL Mismatch",
    "AR_CL_Bucket_Mismatch": "CL Bucket",
    "AR3GWLSTNR_Daemon_Failed_File_Urgent": "GWLS Urgent",
    "AR3GWLSTR_File_Processing": "GWLS Process",
    "AR9PYMRCTUPD_Daemon_Processing_Alert": "AR9 Alert",
    "AR_ATB_GL_Recon": "ATB GL Recon",
    "AR_Accounts_Stuck_Trial_Period": "Trial Stuck",
    "AR_DD_Validation_Missing_Entries": "DD Val Missing",
    "AR_FAILURE_Entries_Missing": "FAIL Missing",
    "AR_BCC_Missing_Subscription_DD_Rejection": "BCC DD Reject",
    "AR_Cost_Center_Change_Report": "Cost Center",
    "AR_DSPREJ_Not_Journalized": "DSPREJ",
    "AR_Feedback_Payment_Files_Pending": "Fdbk/Pymt Pending",
    "AR_IC289_Variance": "IC289 Var",
    "AR_Nameline1_Null_Name_Data": "Nameline Null",
    "AR_Missing_Invoices_AR_GL": "Missing Invoices",
    "AR_Subscription_Pay_Means_Mismatch_Backend": "Subs Pay Means",
    "AR3GWLSTR_Daemon_Stuck": "GWLS Stuck",
    "AR_DDFeedback_Payment_Errored_Files": "DD/Pymt Error",
    "AR_WriteOff_Exited_Collection_After_Payment": "WriteOff Exit",
}
CHECK_GROUP_ENTRIES = {
    "CRM": {"BAP_Error_Report": "Reports & Alerts"},
    "MCO": {"MCO_System_Files_Cleanup": "Reports & Alerts"},
    "ASOM": {"JVM_Threads_Report": "JVM Monitoring"},
    "Digital": {"BDN_BRN_Report": "Bill Notifications"},
    "ABP": {
        "CL_Collection_No_Request_CM_to_OMS": "CL Reports",
        "CL_BCC_Mercantile_TDX_File_Report": "CL Reports",
        "CL_Collection_Activities_Report": "CL Reports",
        "CL_Collection_Letters_Monitoring": "CL Reports",
        "CL_Health_Check_Report": "CL Reports",
        "CL_Collection_Staggering_Backlog": "CL Reports",
        "CL_Missing_TDX_Transaction": "CL Reports",
        "CL_Receipt_Failure_Monitor": "CL Reports",
        "CM_User_Groups_Mismatch": "CM Reports",
        "CM_Customer_Type_Mismatch": "CM Reports",
        "CM_PCN_BAN_BEN_Status_Mismatch": "CM Reports",
        "AR_AC1_Control_Problematic_Files": "AR Email Reports",
        "AR_BL_Mismatch": "AR Email Reports",
        "AR_CL_Bucket_Mismatch": "AR Email Reports",
        "AR3GWLSTNR_Daemon_Failed_File_Urgent": "AR Email Reports",
        "AR3GWLSTR_File_Processing": "AR Email Reports",
        "AR9PYMRCTUPD_Daemon_Processing_Alert": "AR Email Reports",
        "AR_ATB_GL_Recon": "AR Email Reports",
        "AR_Accounts_Stuck_Trial_Period": "AR Email Reports",
        "AR_DD_Validation_Missing_Entries": "AR Email Reports",
        "AR_FAILURE_Entries_Missing": "AR Email Reports",
        "AR_BCC_Missing_Subscription_DD_Rejection": "AR Email Reports",
        "AR_Cost_Center_Change_Report": "AR Email Reports",
        "AR_DSPREJ_Not_Journalized": "AR Email Reports",
        "AR_Feedback_Payment_Files_Pending": "AR Email Reports",
        "AR_IC289_Variance": "AR Email Reports",
        "AR_Nameline1_Null_Name_Data": "AR Email Reports",
        "AR_Missing_Invoices_AR_GL": "AR Email Reports",
        "AR_Subscription_Pay_Means_Mismatch_Backend": "AR Email Reports",
        "AR3GWLSTR_Daemon_Stuck": "AR Email Reports",
        "AR_DDFeedback_Payment_Errored_Files": "AR Email Reports",
        "AR_WriteOff_Exited_Collection_After_Payment": "AR Email Reports",
    },
    "TC": {
        "File_System_Usage_Report": "Infrastructure",
        "AC1_Control_Problematic_Files": "AC1 Control",
        "TC_Health_Check": "Health & Sanity",
        "Rerate_Backlog_Status": "Backlogs",
        "TC_Bill_Reject_Status": "Reports & Alerts",
        "TC_Process_Crash": "Alerts",
        "TC_Usage_Backlog_Alert": "Alerts",
        "TC_Thread_Control_Down": "Alerts",
        "AVM1_ES_Alerts": "Alerts",
    },
    "System": {
        "File_System_Usage_Report": "Infrastructure",
    },
}
METRIC_REPORT_CSS = """
.pd-row.row-info .pd-row-name { color: #1E40AF; font-weight: 600; }
.pd-row.row-info .pd-row-badge { background: #DBEAFE; color: #1D4ED8; min-width: 42px; }
.chk-row-home.row-info .chk-bdg-home { background: #DBEAFE; color: #1D4ED8; }
.pd-sum-info {
    background: #DBEAFE; color: #1D4ED8;
    font-size: 9px; font-weight: 700; padding: 1px 6px; border-radius: 4px;
}
.bap-sections { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.bap-section {
    background: #FFFFFF; border: 1px solid #DBEAFE;
    border-radius: 6px; padding: 6px 8px;
}
.bap-section-title {
    font-size: 9px; font-weight: 700; color: #1E3A8A;
    text-transform: uppercase; margin-bottom: 4px; line-height: 1.3;
}
.bap-count-row {
    display: flex; align-items: flex-start; gap: 6px;
    padding: 3px 0; border-bottom: 1px solid #F1F5F9;
}
.bap-count-row:last-child { border-bottom: none; }
.bap-svc-name {
    flex: 1; font-size: 9px; font-weight: 500; color: #334155;
    line-height: 1.25; word-break: break-word;
}
.bap-count-val {
    font-size: 9px; font-weight: 700; color: #1D4ED8; background: #DBEAFE;
    padding: 1px 6px; border-radius: 4px; flex-shrink: 0;
    min-width: 24px; text-align: center;
}
.mco-meta-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 4px 10px;
    margin-bottom: 8px; padding: 6px 8px;
    background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px;
}
.mco-meta-item { font-size: 9px; line-height: 1.35; }
.mco-meta-lbl { font-weight: 700; color: #64748B; }
.mco-meta-val { font-weight: 600; color: #1E293B; }
.mco-metrics-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px; }
.fs-usage-wrap {
    overflow-x: auto; max-height: 280px; overflow-y: auto;
    border: 1px solid #DBEAFE; border-radius: 6px; background: #FFFFFF;
}
.fs-usage-table { width: 100%; border-collapse: collapse; font-size: 9px; }
.fs-usage-table th {
    position: sticky; top: 0; background: #EFF6FF; color: #1E3A8A;
    font-weight: 700; text-align: left; padding: 4px 6px;
    border-bottom: 1px solid #BFDBFE; white-space: nowrap;
}
.fs-usage-table td {
    padding: 3px 6px; border-bottom: 1px solid #F1F5F9;
    color: #334155; vertical-align: top;
}
.fs-usage-table tr:nth-child(even) td { background: #F8FAFC; }
.fs-pct { font-weight: 700; padding: 1px 5px; border-radius: 4px; white-space: nowrap; }
.fs-pct-ok { background: #DCFCE7; color: #15803D; }
.fs-pct-warn { background: #FEF3C7; color: #B45309; }
.fs-pct-high { background: #FEE2E2; color: #DC2626; }
.fs-owner { font-size: 8px; color: #64748B; max-width: 120px; word-break: break-word; }
.jvm-threads-wrap {
    overflow-x: auto; max-height: 320px; overflow-y: auto;
    border: 1px solid #DBEAFE; border-radius: 6px; background: #FFFFFF;
    margin-bottom: 8px;
}
.jvm-threads-table { width: 100%; border-collapse: collapse; font-size: 8px; }
.jvm-threads-table th {
    position: sticky; top: 0; background: #EFF6FF; color: #1E3A8A;
    font-weight: 700; text-align: left; padding: 3px 5px;
    border-bottom: 1px solid #BFDBFE; white-space: nowrap;
}
.jvm-threads-table td { padding: 2px 5px; border-bottom: 1px solid #F1F5F9; color: #334155; }
.jvm-threads-table tr:nth-child(even) td { background: #F8FAFC; }
.jvm-cluster-title {
    font-size: 9px; font-weight: 700; color: #4338CA;
    margin: 6px 0 3px; text-transform: uppercase;
}
.jvm-product-title {
    font-size: 10px; font-weight: 800; color: #1E3A8A;
    margin: 8px 0 4px; padding-bottom: 3px; border-bottom: 1px solid #DBEAFE;
}
.jvm-cell-red { background: #FEE2E2 !important; color: #DC2626; font-weight: 700; }
.jvm-cell-warn { background: #FEF3C7 !important; color: #B45309; font-weight: 700; }
.jvm-cell-ok { background: #DCFCE7 !important; color: #15803D; }
.bdn-metrics-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px; }
.bdn-metrics-grid-5 { display: grid; grid-template-columns: repeat(5, 1fr); gap: 4px; }
.bdn-metric {
    text-align: center; background: #F8FAFC; border: 1px solid #E2E8F0;
    border-radius: 6px; padding: 5px 4px;
}
.bdn-metric-val { font-size: 13px; font-weight: 800; color: #1E293B; line-height: 1.1; }
.bdn-metric-lbl {
    font-size: 8px; font-weight: 700; color: #64748B;
    text-transform: uppercase; margin-top: 2px; letter-spacing: 0.3px;
}
.bdn-metric-bad .bdn-metric-val { color: #DC2626; }
.bdn-metric-bad { background: #FEE2E2; border-color: #FCA5A5; }
.bdn-stuck-box {
    background: #FEF2F2; border: 1px solid #FCA5A5; border-radius: 6px;
    padding: 6px 8px; margin-top: 8px;
}
.bdn-stuck-title {
    font-size: 9px; font-weight: 800; color: #B91C1C;
    text-transform: uppercase; margin-bottom: 4px;
}
.bdn-stuck-file {
    font-size: 8px; color: #7F1D1D; font-family: monospace;
    word-break: break-all; padding: 2px 0; border-bottom: 1px solid #FEE2E2;
}
.bdn-stuck-file:last-child { border-bottom: none; }
.bdn-table-wrap {
    overflow-x: auto; max-height: 240px; overflow-y: auto;
    border: 1px solid #DBEAFE; border-radius: 6px; background: #FFFFFF; margin-top: 6px;
}
.bdn-table { width: 100%; border-collapse: collapse; font-size: 8px; }
.bdn-table th {
    position: sticky; top: 0; background: #EFF6FF; color: #1E3A8A;
    font-weight: 700; text-align: left; padding: 3px 5px;
    border-bottom: 1px solid #BFDBFE; white-space: nowrap;
}
.bdn-table td {
    padding: 2px 5px; border-bottom: 1px solid #F1F5F9;
    color: #334155; vertical-align: top;
}
.bdn-table tr:nth-child(even) td { background: #F8FAFC; }
.bdn-st-ok { color: #15803D; font-weight: 700; }
.bdn-st-fail { color: #DC2626; font-weight: 700; }
.bdn-reason { font-size: 7px; color: #7F1D1D; word-break: break-word; max-width: 340px; }
.tc-alert-box {
    border-radius: 8px; padding: 10px 12px; margin-top: 6px;
    background: #FEF2F2; border: 1px solid #FCA5A5;
}
.tc-alert-headline { font-size: 12px; font-weight: 800; color: #B91C1C; margin-bottom: 6px; }
.tc-alert-grid { display: grid; grid-template-columns: max-content 1fr; gap: 3px 10px; }
.tc-alert-k { font-size: 9px; font-weight: 700; color: #64748B; text-transform: uppercase; }
.tc-alert-v { font-size: 9px; font-weight: 600; color: #1E293B; word-break: break-word; font-family: monospace; }
.tc-alert-msg { font-size: 9px; color: #7F1D1D; margin-top: 6px; white-space: pre-wrap; }
.tc-metrics-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; margin-top: 6px; }
.tc-section { margin-top: 8px; }
.tc-section-title {
    font-size: 9px; font-weight: 800; color: #1E3A8A; text-transform: uppercase;
    letter-spacing: 0.3px; margin: 8px 0 3px; padding-bottom: 2px; border-bottom: 1px solid #DBEAFE;
    display: flex; justify-content: space-between; align-items: center;
}
.tc-section-count { font-size: 8px; font-weight: 700; color: #64748B; }
.tc-section.tc-section-bad .tc-section-title { color: #B91C1C; border-bottom-color: #FCA5A5; }
.tc-table-wrap { overflow-x: auto; max-height: 260px; overflow-y: auto; border: 1px solid #E2E8F0; border-radius: 6px; }
.tc-table { width: 100%; border-collapse: collapse; font-size: 8px; }
.tc-table th {
    position: sticky; top: 0; background: #EFF6FF; color: #1E3A8A;
    font-weight: 700; text-align: left; padding: 3px 5px; white-space: nowrap;
    border-bottom: 1px solid #BFDBFE;
}
.tc-table td { padding: 2px 5px; border-bottom: 1px solid #F1F5F9; color: #334155; white-space: nowrap; }
.tc-table tr:nth-child(even) td { background: #F8FAFC; }
.tc-table .tc-cell-bad { color: #DC2626; font-weight: 700; }
.tc-note { font-size: 8px; color: #92400E; background: #FEF3C7; border: 1px solid #FDE68A; border-radius: 5px; padding: 4px 6px; margin-top: 4px; }
.tc-empty { font-size: 8px; color: #16A34A; padding: 2px 0; }
"""
MCO_METRIC_ORDER = (
    "Total", "Processed", "Failed", "Erred", "Unprocessed",
    "Never Ran", "Waiting", "Cancelled", "Invalid",
)
 
def check_entries(base):
    return {
        "BAP_Error_Report": {
            "pattern": base + "/bap/BAP_Error_Report*",
            "keyword": "",
            "desc": "BAP Pending Records by Service",
        },
        "MCO_System_Files_Cleanup": {
            "pattern": base + "/mco/MCO_System_Files_Cleanup*",
            "keyword": "",
            "desc": "MCO System Files Cleanup Execution",
        },
        "File_System_Usage_Report": {
            "pattern": base + "/filesystem/PROD_FILE_SYSTEM*",
            "keyword": "",
            "desc": "Production File System Usage Report",
        },
        "JVM_Threads_Report": {
            "pattern": base + "/jvm_threads/JVM_Threads_Report*",
            "keyword": "",
            "desc": "OPTUS JVM Threads Report",
        },
        "BDN_BRN_Report": {
            "pattern": base + "/bdn_brn/BDN_BRN_Report*",
            "keyword": "",
            "desc": "Bill Delivery / Ready Notification (E-Bill) Report",
        },
    }
 
def is_metric_report(name):
    return name in METRIC_REPORT_CHECKS
 
def is_jvm_threads_report(name):
    return name in JVM_THREADS_CHECKS
 
def is_bdn_brn_report(name):
    return name in BDN_BRN_CHECKS
 
def is_tc_report(name):
    return name in TC_REPORT_CHECKS
 
def is_abp_email_report(name):
    return name in ABP_EMAIL_ALERT_CHECKS
 
def is_email_alert(name):
    return name in EMAIL_ALERT_CHECKS
 
def info_badge(name, result):
    if (is_metric_report(name) or is_jvm_threads_report(name)
            or is_bdn_brn_report(name) or is_tc_report(name)
            or is_abp_email_report(name)):
        return str(result.get("info_badge", "—"))
    return None
 
def row_badge(name, result, status):
    if (is_jvm_threads_report(name) or is_bdn_brn_report(name)
            or is_tc_report(name) or is_abp_email_report(name)):
        badge = info_badge(name, result)
        if status == "FAIL":
            return "row-fail", badge
        return "row-info", badge
    if is_metric_report(name) and status == "PASS":
        return "row-info", info_badge(name, result)
    return None, None
 
def enrich_result(name, content, filepath, entry):
    if (not is_metric_report(name) and not is_jvm_threads_report(name)
            and not is_bdn_brn_report(name) and not is_tc_report(name)
            and not is_abp_email_report(name)):
        return
    if name == "BAP_Error_Report":
        data = parse_bap_error_report(content, filepath)
        entry["bap_data"] = data
        entry["info_badge"] = str(data.get("total_pending", 0))
    elif name == "MCO_System_Files_Cleanup":
        data = parse_mco_cleanup_report(content, filepath)
        entry["mco_data"] = data
        entry["info_badge"] = data.get("info_badge", "—")
    elif name == "File_System_Usage_Report":
        data = parse_filesystem_usage_report(content, filepath)
        entry["fs_data"] = data
        entry["info_badge"] = data.get("info_badge", "—")
    elif name == "JVM_Threads_Report":
        data = parse_jvm_threads_report(content, filepath)
        entry["jvm_threads_data"] = data
        entry["info_badge"] = data.get("info_badge", "—")
    elif name == "BDN_BRN_Report":
        data = parse_bdn_brn_report(content, filepath)
        entry["bdn_brn_data"] = data
        entry["info_badge"] = data.get("info_badge", "—")
    elif is_tc_report(name):
        _status, data = tc_report_status(name, content, filepath)
        entry["tc_data"] = data
        entry["info_badge"] = data.get("info_badge", "—")
    elif is_abp_email_report(name):
        _status, data = abp_email_report_status(name, content, filepath)
        entry["abp_email_data"] = data
        entry["info_badge"] = data.get("info_badge", "—")
 
def jvm_threads_status(content, filepath):
    data = parse_jvm_threads_report(content, filepath)
    return "FAIL" if data.get("red_count", 0) > 0 else "PASS", data
 
def bdn_brn_status(content, filepath):
    data = parse_bdn_brn_report(content, filepath)
    return ("FAIL" if data.get("issue_count", 0) > 0 else "PASS"), data
 
def tc_report_status(name, content, filepath):
    if name in TC_ALERT_CHECKS:
        data = parse_tc_alert(content, filepath, name)
        return "FAIL", data
    if name in TC_REJECT_CHECKS:
        data = parse_reject_status(content, filepath)
        return "PASS", data
    if name in TC_SECTIONED_CHECKS:
        data = parse_sectioned_report(content, filepath, name)
        return data.get("status", "PASS"), data
    return "PASS", {"info_badge": "OK"}
 
def abp_email_report_status(name, content, filepath):
    data = parse_tc_alert(content, filepath, name)
    return "FAIL", data
 
def get_metric_panel_html(name, result, content):
    if name == "BAP_Error_Report":
        data = result.get("bap_data") or parse_bap_error_report(content, result.get("file") or "")
        if data.get("pending_records") or data.get("pending_on_pending"):
            return '<div class="pd-log-panel">' + render_bap_count_html(data) + '</div>'
        return '<div class="pd-log-panel"><div class="pd-log-warn">No service counts found in report file.</div></div>'
    if name == "MCO_System_Files_Cleanup":
        data = result.get("mco_data") or parse_mco_cleanup_report(content, result.get("file") or "")
        if data.get("metrics") or data.get("process") or data.get("status"):
            return '<div class="pd-log-panel">' + render_mco_cleanup_html(data) + '</div>'
        return '<div class="pd-log-panel"><div class="pd-log-warn">No execution metrics found in report file.</div></div>'
    if name == "File_System_Usage_Report":
        data = result.get("fs_data") or parse_filesystem_usage_report(content, result.get("file") or "")
        if data.get("filesystems"):
            return '<div class="pd-log-panel">' + render_filesystem_usage_html(data) + '</div>'
        return '<div class="pd-log-panel"><div class="pd-log-warn">No file system usage data found in report file.</div></div>'
    if name == "JVM_Threads_Report":
        data = result.get("jvm_threads_data") or parse_jvm_threads_report(content, result.get("file") or "")
        if data.get("sections") or data.get("summary"):
            return '<div class="pd-log-panel">' + render_jvm_threads_html(data) + '</div>'
        return '<div class="pd-log-panel"><div class="pd-log-warn">No JVM thread data found in report file.</div></div>'
    if name == "BDN_BRN_Report":
        data = result.get("bdn_brn_data") or parse_bdn_brn_report(content, result.get("file") or "")
        if data.get("statuses") or data.get("files") or data.get("sms") or data.get("others"):
            return '<div class="pd-log-panel">' + render_bdn_brn_html(data) + '</div>'
        return '<div class="pd-log-panel"><div class="pd-log-warn">No BDN/BRN data found in report file.</div></div>'
    if is_tc_report(name):
        data = result.get("tc_data")
        if data is None:
            _s, data = tc_report_status(name, content, result.get("file") or "")
        return '<div class="pd-log-panel">' + render_tc_report_html(name, data) + '</div>'
    if is_abp_email_report(name):
        data = result.get("abp_email_data")
        if data is None:
            _s, data = abp_email_report_status(name, content, result.get("file") or "")
        return '<div class="pd-log-panel">' + render_tc_alert_html(name, data) + '</div>'
    return ""
 
def _escape_html(text):
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
 
def _extract_td_cells(row):
    cells_raw = re.findall(r"<td[^>]*>(.*?)</td>", row, re.IGNORECASE | re.DOTALL)
    if cells_raw:
        return [re.sub(r"<[^>]+>", "", c).strip() for c in cells_raw]
    parts = re.split(r"<td[^>]*>", row, flags=re.IGNORECASE)
    cells = []
    for part in parts[1:]:
        text = re.split(r"<(?:td|th|/tr)[^>]*>", part, flags=re.IGNORECASE)[0]
        text = re.sub(r"<[^>]+>", "", text).strip()
        if text:
            cells.append(text)
    return cells
 
def _bap_count_item(name, count):
    try:
        return {"name": str(name).strip(), "count": int(count)}
    except (TypeError, ValueError):
        return None
 
def _bap_result(pending_records, pending_on_pending):
    pending_records = [x for x in pending_records if x]
    pending_on_pending = [x for x in pending_on_pending if x]
    pending_records.sort(key=lambda x: (-x["count"], x["name"]))
    pending_on_pending.sort(key=lambda x: (-x["count"], x["name"]))
    total_pending = sum(x["count"] for x in pending_records) + sum(x["count"] for x in pending_on_pending)
    return {
        "pending_records": pending_records,
        "pending_on_pending": pending_on_pending,
        "total_pending": total_pending,
        "service_count": len(pending_records) + len(pending_on_pending),
    }
 
def _parse_bap_count_table(html_chunk):
    items = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html_chunk or "", re.IGNORECASE | re.DOTALL):
        cells = _extract_td_cells(row)
        if len(cells) < 2:
            continue
        name, val = cells[0].strip(), cells[1].strip()
        if name.upper() in ("SERVICE NAME", "SERVICE", "NAME", "COUNT"):
            continue
        if re.match(r"^\d+$", val):
            item = _bap_count_item(name, val)
            if item:
                items.append(item)
    return items
 
def _parse_bap_delimited(content):
    pending_records, pending_on_pending, current = [], [], pending_records
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        upper = line.upper()
        if "PENDING ON PENDING" in upper:
            current = pending_on_pending
            continue
        if "PENDING RECORD COUNT" in upper:
            current = pending_records
            continue
        if "|" in line:
            parts = [p.strip() for p in line.split("|", 1)]
        elif "," in line:
            parts = [p.strip() for p in line.split(",", 1)]
        else:
            continue
        if len(parts) == 2:
            item = _bap_count_item(parts[0], parts[1])
            if item:
                current.append(item)
    return _bap_result(pending_records, pending_on_pending)
 
def parse_bap_error_report(content, filepath=""):
    content = content or ""
    stripped = content.strip()
    if filepath.lower().endswith(".json") or (stripped.startswith("{") and stripped.endswith("}")):
        try:
            data = json.loads(stripped)
            pending_records, pending_on_pending = [], []
            for raw in data.get("pending_records", []):
                item = _bap_count_item(
                    raw.get("service") or raw.get("name") if isinstance(raw, dict) else raw[0],
                    raw.get("count") if isinstance(raw, dict) else raw[1],
                )
                if item:
                    pending_records.append(item)
            for raw in data.get("pending_on_pending", []):
                item = _bap_count_item(
                    raw.get("service") or raw.get("name") if isinstance(raw, dict) else raw[0],
                    raw.get("count") if isinstance(raw, dict) else raw[1],
                )
                if item:
                    pending_on_pending.append(item)
            return _bap_result(pending_records, pending_on_pending)
        except (json.JSONDecodeError, TypeError, ValueError, IndexError, AttributeError):
            pass
    if "<" not in content[:500]:
        return _parse_bap_delimited(content)
    clean = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", content, flags=re.IGNORECASE | re.DOTALL)
    pr_match = re.search(r"Pending Record Count per Service Name.*?(<table[\s\S]*?</table>)", clean, re.IGNORECASE)
    pop_match = re.search(r"Pending on Pending BAP Transactions.*?(<table[\s\S]*?</table>)", clean, re.IGNORECASE)
    pending_records = _parse_bap_count_table(pr_match.group(1)) if pr_match else []
    pending_on_pending = _parse_bap_count_table(pop_match.group(1)) if pop_match else []
    if not pending_records and not pending_on_pending:
        tables = re.findall(r"<table[\s\S]*?</table>", clean, re.IGNORECASE)
        if len(tables) >= 2:
            pending_records = _parse_bap_count_table(tables[0])
            pending_on_pending = _parse_bap_count_table(tables[1])
        elif len(tables) == 1:
            pending_records = _parse_bap_count_table(tables[0])
    return _bap_result(pending_records, pending_on_pending)
 
def render_bap_count_html(bap_data):
    html = '<div class="pd-inline-log"><div class="pd-inline-log-title">BAP Pending Records by Service</div>'
    html += (
        '<div class="pd-inline-log-summary"><span>Total Pending: {}</span>'
        '<span class="pd-sum-info">Services: {}</span></div><div class="bap-sections">'
    ).format(bap_data.get("total_pending", 0), bap_data.get("service_count", 0))
    for key, title in (
        ("pending_records", "Pending Record Count per Service Name"),
        ("pending_on_pending", "Pending on Pending BAP Transactions"),
    ):
        items = bap_data.get(key, [])
        html += '<div class="bap-section"><div class="bap-section-title">{} ({})</div>'.format(
            _escape_html(title), len(items))
        if not items:
            html += '<div class="bap-svc-name">No records</div>'
        else:
            for item in items:
                html += (
                    '<div class="bap-count-row"><span class="bap-svc-name" title="{}">{}</span>'
                    '<span class="bap-count-val">{}</span></div>'
                ).format(_escape_html(item["name"]), _escape_html(item["name"]), item["count"])
        html += "</div>"
    return html + "</div></div>"
 
def _mco_metric_item(label, value):
    try:
        return {"name": str(label).strip(), "count": int(value)}
    except (TypeError, ValueError):
        return None
 
def parse_mco_cleanup_report(content, filepath=""):
    content = content or ""
    result = {"process": "", "execution_id": "", "created": "", "status": "", "notification": "", "metrics": [], "info_badge": "—"}
    stripped = content.strip()
    if filepath.lower().endswith(".json") or (stripped.startswith("{") and stripped.endswith("}")):
        try:
            data = json.loads(stripped)
            result.update({k: data.get(k, "") for k in ("process", "execution_id", "created", "status", "notification")})
            metrics = {k: data[k] for k in MCO_METRIC_ORDER if k in data}
            metrics.update(data.get("metrics", {}))
            result["metrics"] = [_mco_metric_item(k, metrics[k]) for k in MCO_METRIC_ORDER if k in metrics]
            result["metrics"] = [m for m in result["metrics"] if m]
            return _finalize_mco_result(result)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    plain = content
    if "<" in content[:500]:
        plain = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", content, flags=re.IGNORECASE | re.DOTALL)
        plain = re.sub(r"<br\s*/?>", "\n", plain, flags=re.IGNORECASE)
        plain = re.sub(r"</?(tr|div|p|li|h[1-6])[^>]*>", "\n", plain, flags=re.IGNORECASE)
        plain = re.sub(r"<[^>]+>", " ", plain)
    metrics = {}
    for line in plain.splitlines():
        line = re.sub(r"\s+", " ", line.strip())
        if not line:
            continue
        if not result["process"]:
            proc = re.search(r"(System Files Cleanup(?:\s*\([^)]+\))?)", line, re.IGNORECASE)
            if proc:
                result["process"] = proc.group(1).strip()
        for pattern, field in (
            (r"Execution ID:\s*(#?\d+)", "execution_id"),
            (r"Created Date/Time:\s*(.+?)$", "created"),
            (r"^Status:\s*(.+?)$", "status"),
            (r"Execution (#?\d+) was completed", "notification"),
        ):
            if not result[field]:
                m = re.search(pattern, line, re.IGNORECASE)
                if m:
                    result[field] = m.group(1).strip()
        m = re.match(r"^(Total|Unprocessed|Never Ran|Waiting|Erred|Processed|Failed|Cancelled|Invalid)\s*:?\s*(\d+)\s*$", line, re.IGNORECASE)
        if m:
            key = "Never Ran" if m.group(1).lower() == "never ran" else m.group(1).title()
            metrics[key] = int(m.group(2))
    if "<" in content[:500]:
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", content, re.IGNORECASE | re.DOTALL):
            cells = _extract_td_cells(row)
            if len(cells) >= 2 and cells[0] in MCO_METRIC_ORDER and cells[1].isdigit():
                metrics[cells[0]] = int(cells[1])
    result["metrics"] = [_mco_metric_item(k, metrics[k]) for k in MCO_METRIC_ORDER if k in metrics]
    result["metrics"] = [m for m in result["metrics"] if m]
    if not result["notification"] and result["execution_id"]:
        result["notification"] = "Execution {} was completed".format(result["execution_id"])
    return _finalize_mco_result(result)
 
def _finalize_mco_result(result):
    processed = next((m["count"] for m in result["metrics"] if m["name"] == "Processed"), None)
    total = next((m["count"] for m in result["metrics"] if m["name"] == "Total"), None)
    if processed is not None and total is not None:
        result["info_badge"] = "{}/{}".format(processed, total)
    elif result.get("status"):
        result["info_badge"] = result["status"][:10]
    return result
 
def render_mco_cleanup_html(mco_data):
    html = '<div class="pd-inline-log"><div class="pd-inline-log-title">MCO System Files Cleanup</div>'
    if mco_data.get("notification"):
        html += '<div class="pd-inline-log-summary"><span class="pd-sum-info">{}</span></div>'.format(
            _escape_html(mco_data["notification"]))
    html += '<div class="mco-meta-grid">'
    for label, val in (
        ("Process", mco_data.get("process") or "System Files Cleanup"),
        ("Execution ID", mco_data.get("execution_id") or "—"),
        ("Created", mco_data.get("created") or "—"),
        ("Status", mco_data.get("status") or "—"),
    ):
        html += '<div class="mco-meta-item"><span class="mco-meta-lbl">{}:</span> <span class="mco-meta-val">{}</span></div>'.format(
            _escape_html(label), _escape_html(val))
    html += '</div><div class="bap-section"><div class="bap-section-title">Execution Metrics</div>'
    metrics = mco_data.get("metrics", [])
    if not metrics:
        html += '<div class="bap-svc-name">No metrics found in report file.</div>'
    else:
        html += '<div class="mco-metrics-grid">'
        for item in metrics:
            html += '<div class="bap-count-row"><span class="bap-svc-name">{}</span><span class="bap-count-val">{}</span></div>'.format(
                _escape_html(item["name"]), item["count"])
        html += "</div>"
    return html + "</div></div>"
 
def _parse_used_pct(value):
    try:
        return int(float(str(value).strip().rstrip("%")))
    except (TypeError, ValueError):
        return None
 
def _fs_pct_class(pct):
    if pct >= 85:
        return "fs-pct-high"
    if pct >= 75:
        return "fs-pct-warn"
    return "fs-pct-ok"
 
def _filesystem_row(machine, filesystem, size, used, avail, used_pct, owner_tag):
    pct = _parse_used_pct(used_pct)
    if not machine or pct is None:
        return None
    return {
        "machine": str(machine).strip(),
        "filesystem": str(filesystem or "").strip(),
        "size": str(size or "").strip(),
        "used": str(used or "").strip(),
        "avail": str(avail or "").strip(),
        "used_pct": pct,
        "owner_tag": str(owner_tag or "").strip(),
    }
 
def _finalize_fs_result(rows, title=""):
    rows = [r for r in rows if r]
    rows.sort(key=lambda x: (-x["used_pct"], x["machine"], x["filesystem"]))
    max_pct = rows[0]["used_pct"] if rows else 0
    return {
        "title": title or "OPTUS File System Usage Report",
        "filesystems": rows,
        "max_used_pct": max_pct,
        "filesystem_count": len(rows),
        "info_badge": "{}%".format(max_pct) if rows else "—",
    }
 
def parse_filesystem_usage_report(content, filepath=""):
    content = content or ""
    title = ""
    stripped = content.strip()
    if filepath.lower().endswith(".json") or (stripped.startswith("{") and stripped.endswith("}")):
        try:
            data = json.loads(stripped)
            title = data.get("title", "")
            rows = []
            for raw in data.get("filesystems", data.get("entries", [])):
                if isinstance(raw, dict):
                    row = _filesystem_row(
                        raw.get("machine"), raw.get("filesystem") or raw.get("file_system"),
                        raw.get("size"), raw.get("used"), raw.get("avail"),
                        raw.get("used_pct") or raw.get("used_percent"),
                        raw.get("owner_tag") or raw.get("owner"),
                    )
                    if row:
                        rows.append(row)
            return _finalize_fs_result(rows, title)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    if "<" not in content[:500]:
        rows = []
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "FILE SYSTEM" in line.upper() and "REPORT" in line.upper():
                title = line
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 7:
                row = _filesystem_row(*parts[:7])
                if row:
                    rows.append(row)
        return _finalize_fs_result(rows, title)
    clean = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", content, flags=re.IGNORECASE | re.DOTALL)
    title_match = re.search(r"(OPTUS File System Usage Report|PROD FILE SYSTEM ALERT)", clean, re.IGNORECASE)
    if title_match:
        title = title_match.group(1)
    rows = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", clean, re.IGNORECASE | re.DOTALL):
        cells = _extract_td_cells(row_html)
        if len(cells) < 6 or cells[0].upper() in ("MACHINE", "HOST", "SERVER"):
            continue
        row = _filesystem_row(*cells[:7]) if len(cells) >= 7 else _filesystem_row(*cells[:6], "")
        if row:
            rows.append(row)
    return _finalize_fs_result(rows, title)
 
def render_filesystem_usage_html(fs_data):
    rows = fs_data.get("filesystems", [])
    html = '<div class="pd-inline-log"><div class="pd-inline-log-title">{}</div>'.format(
        _escape_html(fs_data.get("title") or "File System Usage"))
    html += (
        '<div class="pd-inline-log-summary"><span>File Systems: {}</span>'
        '<span class="pd-sum-info">Max Used: {}%</span></div>'
    ).format(fs_data.get("filesystem_count", 0), fs_data.get("max_used_pct", 0))
    if not rows:
        return html + '<div class="pd-log-warn">No file system entries found in report file.</div></div>'
    html += (
        '<div class="fs-usage-wrap"><table class="fs-usage-table"><thead><tr>'
        '<th>Machine</th><th>File System</th><th>Size</th><th>Used</th>'
        '<th>Avail</th><th>Used %</th><th>Owner Tag</th></tr></thead><tbody>'
    )
    for row in rows:
        pct_cls = _fs_pct_class(row["used_pct"])
        html += (
            "<tr><td>{m}</td><td>{fs}</td><td>{sz}</td><td>{u}</td><td>{a}</td>"
            '<td><span class="fs-pct {cls}">{pct}%</span></td><td class="fs-owner">{o}</td></tr>'
        ).format(
            m=_escape_html(row["machine"]), fs=_escape_html(row["filesystem"]),
            sz=_escape_html(row["size"]), u=_escape_html(row["used"]),
            a=_escape_html(row["avail"]), cls=pct_cls, pct=row["used_pct"],
            o=_escape_html(row["owner_tag"]),
        )
    return html + "</tbody></table></div></div>"
 
def _decode_report_content(content, filepath=""):
    content = content or ""
    filepath = filepath or ""
    if filepath.lower().endswith(".eml") or "Content-Type: text/html" in content[:4000]:
        html_start = re.search(r"<html[\s>]", content, re.IGNORECASE)
        if html_start:
            content = content[html_start.start():]
        try:
            content = quopri.decodestring(content.encode("latin-1", errors="ignore")).decode(
                "utf-8", errors="ignore")
        except Exception:
            content = content.replace("=3D", "=").replace("=3d", "=")
            content = re.sub(r"=\r?\n", "", content)
    else:
        content = content.replace("=3D", "=").replace("=3d", "=")
        content = re.sub(r"=\r?\n", "", content)
    return content
 
def _is_green_bg(bgcolor):
    if not bgcolor:
        return True
    norm = bgcolor.strip().lstrip("#").upper()
    return norm in ("08CD08", "08cd08".upper())
 
def _extract_td_cells_with_bg(row):
    cells = []
    for match in re.finditer(r"<td([^>]*)>(.*?)</td>", row or "", re.IGNORECASE | re.DOTALL):
        attrs, inner = match.group(1), match.group(2)
        bg_match = re.search(r'bgcolor=["\']?([^"\'>\s]+)', attrs, re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", inner).strip()
        cells.append({"text": text, "bgcolor": bg_match.group(1) if bg_match else ""})
    return cells
 
def _row_is_red(cells):
    for cell in cells:
        if cell["text"] and not _is_green_bg(cell["bgcolor"]):
            return True
    return False
 
def _cluster_title_from_row(row_html):
    h2 = re.search(r"<h2[^>]*>(.*?)</h2>", row_html or "", re.IGNORECASE | re.DOTALL)
    if h2:
        return re.sub(r"<[^>]+>", "", h2.group(1)).strip().strip("-").strip()
    cells = _extract_td_cells_with_bg(row_html)
    if len(cells) == 1:
        text = cells[0]["text"].strip()
        stripped = text.strip("-").strip()
        if stripped and text.startswith("-") and text.endswith("-"):
            return stripped
    return None
 
def parse_jvm_threads_report(content, filepath=""):
    content = _decode_report_content(content, filepath)
    clean = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", content, flags=re.IGNORECASE | re.DOTALL)
    summary = ""
    summary_match = re.search(
        r"Summary of RED Items[\s\S]*?<td[^>]*>(.*?)</td>",
        clean, re.IGNORECASE,
    )
    if summary_match:
        summary = re.sub(r"<[^>]+>", "", summary_match.group(1)).strip()
    sections = []
    current_product = None
    current_cluster = None
    red_count = 0
    server_count = 0
    parts = re.split(r"(<h1[^>]*>.*?</h1>|<h2[^>]*>.*?</h2>|<table[\s\S]*?</table>)", clean, flags=re.IGNORECASE)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", part, re.IGNORECASE | re.DOTALL)
        if h1 and part.lower().startswith("<h1"):
            title = re.sub(r"<[^>]+>", "", h1.group(1)).strip()
            title = title.replace("#", "").strip()
            if "summary" in title.lower() and "red" in title.lower():
                current_product = None
                current_cluster = None
                continue
            current_product = {"product": title, "clusters": []}
            sections.append(current_product)
            current_cluster = None
            continue
        if part.lower().startswith("<h2") and current_product is not None:
            h2 = re.search(r"<h2[^>]*>(.*?)</h2>", part, re.IGNORECASE | re.DOTALL)
            if h2:
                cluster_name = re.sub(r"<[^>]+>", "", h2.group(1)).strip().strip("-").strip()
                current_cluster = {"name": cluster_name, "servers": []}
                current_product["clusters"].append(current_cluster)
            continue
        if not part.lower().startswith("<table"):
            continue
        if current_product is None:
            continue
        for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", part, re.IGNORECASE | re.DOTALL):
            if "<th" in row_html.lower():
                continue
            cluster_name = _cluster_title_from_row(row_html)
            if cluster_name:
                current_cluster = {"name": cluster_name, "servers": []}
                current_product["clusters"].append(current_cluster)
                continue
            if current_cluster is None:
                current_cluster = {"name": "Default", "servers": []}
                current_product["clusters"].append(current_cluster)
            cells = _extract_td_cells_with_bg(row_html)
            if len(cells) < 8:
                continue
            if cells[0]["text"].upper() in JVM_THREAD_HEADERS:
                continue
            values = [c["text"] for c in cells[:8]]
            is_red = _row_is_red(cells[:8])
            if is_red:
                red_count += 1
            server_count += 1
            current_cluster["servers"].append({
                "server_name": values[0],
                "idle": values[1],
                "total": values[2],
                "standby": values[3],
                "completed": values[4],
                "stuck": values[5],
                "hogging": values[6],
                "queue": values[7],
                "is_red": is_red,
            })
    if not summary:
        summary = "No issues found" if red_count == 0 else "{} RED item(s)".format(red_count)
    return {
        "title": "OPTUS JVM Threads Report",
        "summary": summary,
        "red_count": red_count,
        "server_count": server_count,
        "sections": sections,
        "info_badge": "{} RED".format(red_count),
    }
 
def _jvm_cell_class(cell_text, is_red_row, col_idx):
    if not is_red_row:
        return "jvm-cell-ok"
    if col_idx >= 5:
        return "jvm-cell-red"
    return "jvm-cell-warn"
 
def render_jvm_threads_html(data):
    html = '<div class="pd-inline-log"><div class="pd-inline-log-title">{}</div>'.format(
        _escape_html(data.get("title") or "JVM Threads Report"))
    summary_cls = "pd-sum-info" if data.get("red_count", 0) == 0 else "pd-sum-fail"
    html += (
        '<div class="pd-inline-log-summary">'
        '<span>Summary: {}</span>'
        '<span class="{}">{} RED</span>'
        '<span class="pd-sum-info">Servers: {}</span>'
        '</div>'
    ).format(
        _escape_html(data.get("summary") or "—"),
        summary_cls, data.get("red_count", 0), data.get("server_count", 0))
    sections = data.get("sections", [])
    if not sections:
        html += '<div class="pd-log-warn">No JVM thread sections found.</div></div>'
        return html
    cols = ["Server", "Idle", "Total", "Standby", "Completed", "Stuck", "Hogging", "Queue"]
    for section in sections:
        html += '<div class="jvm-product-title">{}</div>'.format(_escape_html(section.get("product", "")))
        for cluster in section.get("clusters", []):
            servers = cluster.get("servers", [])
            if not servers:
                continue
            html += '<div class="jvm-cluster-title">{} ({} servers)</div>'.format(
                _escape_html(cluster.get("name", "")), len(servers))
            html += (
                '<div class="jvm-threads-wrap"><table class="jvm-threads-table">'
                '<thead><tr>{}</tr></thead><tbody>'
            ).format("".join("<th>{}</th>".format(c) for c in cols))
            for srv in servers:
                is_red = srv.get("is_red", False)
                vals = [
                    srv.get("server_name", ""), srv.get("idle", ""), srv.get("total", ""),
                    srv.get("standby", ""), srv.get("completed", ""), srv.get("stuck", ""),
                    srv.get("hogging", ""), srv.get("queue", ""),
                ]
                html += "<tr>"
                for idx, val in enumerate(vals):
                    cls = _jvm_cell_class(val, is_red, idx)
                    html += '<td class="{}">{}</td>'.format(cls, _escape_html(val))
                html += "</tr>"
            html += "</tbody></table></div>"
    return html + "</div>"
 
# --------------------------------------------------------------------------- #
# BDN/BRN — Bill Delivery / Bill Ready Notification (E-Bill) report
# --------------------------------------------------------------------------- #
def _bdn_to_plaintext(content):
    """Convert an HTML/eml report body to a plain-text, table-aware form."""
    if "<" not in content[:2000]:
        return content
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", content, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</t[dh]>", "\t", text, flags=re.IGNORECASE)
    text = re.sub(r"</tr>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(p|div|h[1-6]|li|table|tbody|thead|pre)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'"))
    return text
 
def _bdn_split_cols(line):
    """Split a table row on tabs, or on runs of 2+ spaces as a fallback."""
    if "\t" in line:
        cols = [c.strip() for c in line.split("\t")]
    else:
        cols = [c.strip() for c in re.split(r"\s{2,}", line.strip())]
    return [c for c in cols if c != ""]
 
def _bdn_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0
 
def parse_bdn_brn_report(content, filepath=""):
    content = _decode_report_content(content or "", filepath)
    text = _bdn_to_plaintext(content)
    lines = [ln.rstrip() for ln in text.splitlines()]
    result = {
        "report_time": "",
        "files": {"inuse": 0, "new": 0, "damaged": 0},
        "statuses": [],           # [(label, count), ...]
        "total_files": 0,
        "stuck_files": [],        # [path, ...]
        "sms": [],                # [{date, method, status, count}, ...]
        "others": [],
        "failed_bar": [],         # [{date, bar, count, reason}, ...]
        "issue_count": 0,
        "info_badge": "OK",
    }
    n = len(lines)
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        upper = line.upper()
        if "E-BILL PROCESS FILES REPORT" in upper and "AT" in upper:
            m = re.search(r"at\s*:?\s*(.+)$", line, re.IGNORECASE)
            if m:
                result["report_time"] = m.group(1).strip()
        # E-Bill file counts: header "INUSE NEW DAMAGED" then a row of 3 ints.
        if "INUSE" in upper and "DAMAGED" in upper:
            for j in range(i + 1, min(i + 5, n)):
                nums = re.findall(r"\d+", lines[j])
                if re.fullmatch(r"[-\s]+", lines[j].strip()):
                    continue
                if len(nums) >= 3:
                    result["files"] = {
                        "inuse": _bdn_int(nums[0]),
                        "new": _bdn_int(nums[1]),
                        "damaged": _bdn_int(nums[2]),
                    }
                    break
        # File statuses: header with READY ... COMPLETE ... REJECTED then row of ints.
        if "READY" in upper and "COMPLETE" in upper and "REJECTED" in upper:
            labels = ["READY", "STARTED", "IN PROCESS", "COMPLETE", "REJECTED"]
            for j in range(i + 1, min(i + 5, n)):
                if re.fullmatch(r"[-\s]+", lines[j].strip()):
                    continue
                nums = re.findall(r"\d+", lines[j])
                if len(nums) >= 5:
                    result["statuses"] = list(zip(labels, [_bdn_int(x) for x in nums[:5]]))
                    break
        if "TOTAL NUMBER OF FILES" in upper:
            m = re.search(r"(\d+)", line)
            if m:
                result["total_files"] = _bdn_int(m.group(1))
        # Stuck / damaged files being moved for reprocess.
        if "MOVING THE BELOW STUCK AND DAMAGED FILES" in upper:
            for j in range(i + 1, n):
                cand = lines[j].strip()
                if not cand or set(cand) <= set("_-="):
                    continue
                if cand.startswith("/") or re.search(r"\.(inuse|tar\.gz|gz|xml|dat)$", cand, re.IGNORECASE):
                    result["stuck_files"].append(cand)
                elif result["stuck_files"]:
                    break
    result["sms"] = _bdn_parse_notif_table(lines, r"BILL NOTIFICATION STATUS REPORT\s*\(SMS\)")
    result["others"] = _bdn_parse_notif_table(lines, r"BILL NOTIFICATION STATUS REPORT\s*\(OTHERS\)")
    result["failed_bar"] = _bdn_parse_failed_bar(lines)
    damaged = result["files"].get("damaged", 0)
    rejected = next((c for lbl, c in result["statuses"] if lbl == "REJECTED"), 0)
    issue = damaged + rejected + len(result["stuck_files"])
    result["issue_count"] = issue
    if issue > 0:
        result["info_badge"] = "{} stuck".format(issue)
    else:
        result["info_badge"] = "OK"
    return result
 
def _bdn_parse_notif_table(lines, header_regex):
    rows = []
    n = len(lines)
    start = None
    for i, line in enumerate(lines):
        if re.search(header_regex, line, re.IGNORECASE):
            start = i
            break
    if start is None:
        return rows
    seen_header = False
    for j in range(start + 1, n):
        line = lines[j].strip()
        if not line:
            continue
        upper = line.upper()
        if "BILL_CLOS" in upper or ("BILL_DELIVERY" in upper and "COUNT" in upper):
            seen_header = True
            continue
        # Stop when a new named section begins.
        if line.upper().startswith("MESSAGE") or "REPORT AT" in upper or "REPORT (" in upper:
            if seen_header:
                break
            continue
        cols = _bdn_split_cols(line)
        if len(cols) >= 4 and re.search(r"\d", cols[-1]):
            rows.append({
                "date": cols[0], "method": cols[1],
                "status": cols[2], "count": cols[3],
            })
    return rows
 
def _bdn_parse_failed_bar(lines):
    rows = []
    n = len(lines)
    start = None
    for i, line in enumerate(lines):
        if re.search(r"PERMANENT NOTIFICATION FAILED BAR REPORT", line, re.IGNORECASE):
            start = i
            break
    if start is None:
        return rows
    for j in range(start + 1, n):
        line = lines[j].strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("BILL_CLOS") or ("BAR" in upper and "FAILURE_COUNT" in upper):
            continue
        if line.upper().startswith("MESSAGE") or "REPORT AT" in upper:
            break
        cols = _bdn_split_cols(line)
        if len(cols) >= 3 and re.match(r"\d", cols[0]) and re.search(r"\d", cols[2]):
            rows.append({
                "date": cols[0], "bar": cols[1],
                "count": cols[2],
                "reason": " ".join(cols[3:]) if len(cols) > 3 else "",
            })
    return rows
 
def _bdn_metric_cell(label, value, bad=False):
    cls = "bdn-metric bdn-metric-bad" if bad else "bdn-metric"
    return (
        '<div class="{cls}"><div class="bdn-metric-val">{val}</div>'
        '<div class="bdn-metric-lbl">{lbl}</div></div>'
    ).format(cls=cls, val=_escape_html(value), lbl=_escape_html(label))
 
def _bdn_notif_table_html(title, rows):
    if not rows:
        return ""
    html = '<div class="bap-section-title">{}</div>'.format(_escape_html(title))
    html += ('<div class="bdn-table-wrap"><table class="bdn-table"><thead><tr>'
             '<th>Bill Close</th><th>Delivery Method</th><th>Status</th><th>Count</th>'
             '</tr></thead><tbody>')
    for r in rows:
        st = (r.get("status") or "").upper()
        st_cls = "bdn-st-fail" if "FAIL" in st else ("bdn-st-ok" if "COMPLET" in st else "")
        html += (
            '<tr><td>{d}</td><td>{m}</td><td class="{sc}">{s}</td><td>{c}</td></tr>'
        ).format(d=_escape_html(r.get("date", "")), m=_escape_html(r.get("method", "")),
                 sc=st_cls, s=_escape_html(r.get("status", "")), c=_escape_html(r.get("count", "")))
    return html + "</tbody></table></div>"
 
def render_bdn_brn_html(data):
    files = data.get("files", {})
    issue = data.get("issue_count", 0)
    html = '<div class="pd-inline-log"><div class="pd-inline-log-title">BDN/BRN — Bill Notification &amp; E-Bill Files</div>'
    summary_cls = "pd-sum-info" if issue == 0 else "pd-sum-fail"
    html += (
        '<div class="pd-inline-log-summary">'
        '<span>Report: {t}</span>'
        '<span class="pd-sum-info">Total Files: {tot}</span>'
        '<span class="{scls}">{iss} stuck/damaged/rejected</span>'
        '</div>'
    ).format(
        t=_escape_html(data.get("report_time") or "—"),
        tot=_escape_html(data.get("total_files", 0)),
        scls=summary_cls, iss=issue)
    # E-Bill process file counts.
    html += '<div class="bap-section"><div class="bap-section-title">E-Bill Process Files</div><div class="bdn-metrics-grid">'
    html += _bdn_metric_cell("In Use", files.get("inuse", 0))
    html += _bdn_metric_cell("New", files.get("new", 0))
    html += _bdn_metric_cell("Damaged", files.get("damaged", 0), bad=files.get("damaged", 0) > 0)
    html += "</div></div>"
    # File status breakdown.
    statuses = data.get("statuses", [])
    if statuses:
        html += '<div class="bap-section"><div class="bap-section-title">File Statuses</div><div class="bdn-metrics-grid-5">'
        for label, count in statuses:
            bad = label == "REJECTED" and count > 0
            html += _bdn_metric_cell(label, count, bad=bad)
        html += "</div></div>"
    # Stuck / damaged files.
    stuck = data.get("stuck_files", [])
    if stuck:
        html += '<div class="bdn-stuck-box"><div class="bdn-stuck-title">Stuck / Damaged Files Moved for Reprocess ({})</div>'.format(len(stuck))
        for fp in stuck:
            html += '<div class="bdn-stuck-file">{}</div>'.format(_escape_html(fp))
        html += "</div>"
    # Notification tables.
    sms_html = _bdn_notif_table_html("Last 7 Days Bill Notification (SMS)", data.get("sms", []))
    others_html = _bdn_notif_table_html("Last 7 Days Bill Notification (Others)", data.get("others", []))
    if sms_html or others_html:
        html += '<div class="bap-section">' + sms_html + others_html + "</div>"
    # Permanent notification failed bar report.
    failed_bar = data.get("failed_bar", [])
    if failed_bar:
        html += '<div class="bap-section"><div class="bap-section-title">Permanent Notification Failed Bar ({})</div>'.format(len(failed_bar))
        html += ('<div class="bdn-table-wrap"><table class="bdn-table"><thead><tr>'
                 '<th>Bill Close</th><th>Bar</th><th>Failures</th><th>Reason</th>'
                 '</tr></thead><tbody>')
        for r in failed_bar:
            html += (
                '<tr><td>{d}</td><td>{b}</td><td class="bdn-st-fail">{c}</td>'
                '<td class="bdn-reason">{rsn}</td></tr>'
            ).format(d=_escape_html(r.get("date", "")), b=_escape_html(r.get("bar", "")),
                     c=_escape_html(r.get("count", "")), rsn=_escape_html((r.get("reason", "") or "")[:400]))
        html += "</tbody></table></div></div>"
    return html + "</div>"
 
# --------------------------------------------------------------------------- #
# Outlook .msg reader (used for TC email reports)
# --------------------------------------------------------------------------- #
def load_content(filepath):
    """Read a report file as text, transparently decoding Outlook .msg files."""
    if not filepath:
        return ""
    if filepath.lower().endswith(".msg"):
        return read_msg_text(filepath)
    with open(filepath, "r", errors="ignore") as f:
        return f.read()
 
def _decode_msg_stream(ole, path, upper_leaf):
    try:
        raw = ole.openstream(path).read()
    except Exception:
        return ""
    if upper_leaf.endswith("001F"):        # PT_UNICODE (UTF-16LE)
        return raw.decode("utf-16-le", "ignore")
    return raw.decode("cp1252", "ignore")  # PT_STRING8
 
def read_msg_text(filepath):
    """Extract subject + body (+ text/csv attachments) from an Outlook .msg."""
    try:
        import olefile
    except Exception:
        return _scrape_msg_bytes(filepath)
    try:
        ole = olefile.OleFileIO(filepath)
    except Exception:
        return _scrape_msg_bytes(filepath)
    subject, body = "", ""
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
                attachments.setdefault(store, {})["name"] = _decode_msg_stream(ole, entry, up)
            elif up.startswith("__SUBSTG1.0_3701"):
                store = entry[0]
                try:
                    attachments.setdefault(store, {})["data"] = ole.openstream(entry).read()
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
        name = (info.get("name") or "").strip()
        data = info.get("data")
        if data and name.lower().endswith((".csv", ".txt", ".log", ".dat")):
            try:
                parts.append("[ATTACHMENT: {}]\n{}".format(name, data.decode("utf-8", "ignore")))
            except Exception:
                pass
    return "\n\n".join(p for p in parts if p)
 
def _scrape_msg_bytes(filepath):
    """Last-resort .msg reader: pull UTF-16 text runs straight from the bytes."""
    try:
        with open(filepath, "rb") as f:
            raw = f.read()
    except Exception:
        return ""
    text = raw.decode("utf-16-le", "ignore")
    text = re.sub(r"[^\t\r\n\x20-\x7e]", "", text)
    return text
 
# --------------------------------------------------------------------------- #
# TC report content helpers
# --------------------------------------------------------------------------- #
_TC_CAUTION_RE = re.compile(r"CAUTION\s*:\s*This email is from an external source[^\n]*\n?", re.IGNORECASE)
_TC_FOOTER_RE = re.compile(r"\n-\s*Tasker\s*$", re.IGNORECASE)
 
def _tc_prep_text(content, filepath=""):
    content = content or ""
    if "<html" in content[:2000].lower() or "<table" in content[:4000].lower():
        content = _bdn_to_plaintext(content)
    content = _TC_CAUTION_RE.sub("", content)
    content = _TC_FOOTER_RE.sub("", content)
    return content
 
def _tc_split_cells(line):
    cells = [c.strip() for c in line.split("\t")]
    while cells and cells[-1] == "":
        cells.pop()
    return cells
 
def _tc_subject(text):
    m = re.search(r"^SUBJECT:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else ""
 
# --------------------------------------------------------------------------- #
# Generic sectioned tab-delimited report (AC1 Control, TC Health, Rerate)
# --------------------------------------------------------------------------- #
def parse_sectioned_report(content, filepath="", name=""):
    text = _tc_prep_text(content, filepath)
    subject = _tc_subject(text)
    body = re.sub(r"^SUBJECT:.*$", "", text, flags=re.IGNORECASE | re.MULTILINE)
    blocks = re.split(r"\n\s*_{3,}\s*\n", body)
    sections = []
    notes = []
    for block in blocks:
        lines = [ln.rstrip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        title = ""
        tab_lines = []
        for ln in lines:
            if "\t" in ln:
                tab_lines.append(ln)
            elif not tab_lines and not title:
                title = ln.strip()
            elif not tab_lines:
                title = (title + " " + ln.strip()).strip()
        if not tab_lines:
            note = " ".join(lines).strip()
            if note and not re.match(r"(Description|Owner)\s*:", note, re.IGNORECASE):
                notes.append(note)
            continue
        headers = _tc_split_cells(tab_lines[0])
        rows = []
        for ln in tab_lines[1:]:
            cells = _tc_split_cells(ln)
            if cells:
                rows.append(cells)
        sections.append({"title": title, "headers": headers, "rows": rows})
    data = {
        "subject": subject,
        "sections": sections,
        "notes": notes,
    }
    _finalize_sectioned(name, data)
    return data
 
def _section_col(section, col_name):
    headers = [h.upper() for h in section.get("headers", [])]
    try:
        return headers.index(col_name.upper())
    except ValueError:
        return -1
 
def _finalize_sectioned(name, data):
    sections = data["sections"]
    status = "PASS"
    badge = "OK"
    bad_titles = set()
    if name == "AC1_Control_Problematic_Files":
        problem_files = 0
        skip = {"START_DATE", "END_DATE"}
        for sec in sections:
            title = (sec.get("title") or "").upper()
            if title in skip:
                continue
            rows = sec.get("rows", [])
            if not rows:
                continue
            idx = _section_col(sec, "FILE_COUNT")
            if idx >= 0:
                for r in rows:
                    if idx < len(r):
                        problem_files += _bdn_int(re.sub(r"[^\d]", "", r[idx]) or 0)
                bad_titles.add(sec.get("title"))
            else:
                # identifier / mismatch tables — any row is a problem
                problem_files += len(rows)
                bad_titles.add(sec.get("title"))
        status = "FAIL" if bad_titles else "PASS"
        badge = "{} files".format(problem_files) if problem_files else ("issues" if bad_titles else "OK")
    elif name == "TC_Health_Check":
        issues = 0
        for sec in sections:
            title = (sec.get("title") or "").upper()
            rows = sec.get("rows", [])
            if title == "THR_CTRL":
                sidx = _section_col(sec, "THREAD_STATUS")
                down = [r for r in rows if sidx >= 0 and sidx < len(r) and r[sidx].upper() == "DN"]
                if down:
                    issues += len(down)
                    bad_titles.add(sec.get("title"))
            elif title == "ERROR_FILE" and rows:
                issues += len(rows)
                bad_titles.add(sec.get("title"))
            elif title == "TRB1_SUB_ERRS" and rows:
                issues += len(rows)
                bad_titles.add(sec.get("title"))
        status = "FAIL" if bad_titles else "PASS"
        badge = "{} issue(s)".format(issues) if issues else "OK"
    elif name == "Rerate_Backlog_Status":
        total = 0
        for sec in sections:
            if "OVER_ALL_RERATE_BACKLOG" in (sec.get("title") or "").upper():
                idx = _section_col(sec, "OVER_ALL_BACKLOG")
                for r in sec.get("rows", []):
                    if idx >= 0 and idx < len(r):
                        total += _bdn_int(re.sub(r"[^\d]", "", r[idx]) or 0)
        status = "PASS"
        badge = _tc_human_num(total) if total else "0"
    data["status"] = status
    data["info_badge"] = badge
    data["bad_titles"] = bad_titles
 
def _tc_human_num(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    if n >= 1_000_000:
        return "{:.1f}M".format(n / 1_000_000)
    if n >= 1_000:
        return "{:.1f}K".format(n / 1_000)
    return str(n)
 
def _tc_render_section(sec, bad_titles):
    title = sec.get("title") or "Section"
    headers = sec.get("headers", [])
    rows = sec.get("rows", [])
    is_bad = title in bad_titles
    cls = "tc-section tc-section-bad" if is_bad else "tc-section"
    html = '<div class="{cls}"><div class="tc-section-title"><span>{t}</span><span class="tc-section-count">{n} row(s)</span></div>'.format(
        cls=cls, t=_escape_html(title), n=len(rows))
    if not rows:
        html += '<div class="tc-empty">No records.</div></div>'
        return html
    html += '<div class="tc-table-wrap"><table class="tc-table"><thead><tr>'
    for h in headers:
        html += "<th>{}</th>".format(_escape_html(h))
    html += "</tr></thead><tbody>"
    for r in rows:
        html += "<tr>"
        for i in range(len(headers)):
            val = r[i] if i < len(r) else ""
            cell_cls = ' class="tc-cell-bad"' if (is_bad and val.upper() in ("DN", "AF")) else ""
            html += "<td{}>{}</td>".format(cell_cls, _escape_html(val))
        html += "</tr>"
    html += "</tbody></table></div></div>"
    return html
 
def render_sectioned_report_html(name, data):
    title_map = {
        "AC1_Control_Problematic_Files": "AC1 Control — Problematic Files (EP & CM)",
        "TC_Health_Check": "Optus Production TC Health Check",
        "Rerate_Backlog_Status": "Optus Production — Rerate Backlog Status",
    }
    title = title_map.get(name, data.get("subject") or "TC Report")
    sections = data.get("sections", [])
    bad_titles = data.get("bad_titles", set())
    n_bad = len(bad_titles)
    summary_cls = "pd-sum-fail" if data.get("status") == "FAIL" else "pd-sum-info"
    html = '<div class="pd-inline-log"><div class="pd-inline-log-title">{}</div>'.format(_escape_html(title))
    html += (
        '<div class="pd-inline-log-summary">'
        '<span>Sections: {n}</span>'
        '<span class="{scls}">{badge}</span>'
        '</div>'
    ).format(n=len(sections), scls=summary_cls, badge=_escape_html(data.get("info_badge", "OK")))
    if n_bad:
        html += '<div class="tc-note">Flagged section(s): {}</div>'.format(
            _escape_html(", ".join(t for t in bad_titles if t)))
    for sec in sections:
        html += _tc_render_section(sec, bad_titles)
    for note in data.get("notes", []):
        html += '<div class="tc-note">{}</div>'.format(_escape_html(note[:300]))
    return html + "</div>"
 
# --------------------------------------------------------------------------- #
# Simple TC alert emails (presence == failure)
# --------------------------------------------------------------------------- #
def parse_tc_alert(content, filepath="", name=""):
    text = _tc_prep_text(content, filepath)
    subject = _tc_subject(text)
    body = re.sub(r"^SUBJECT:.*$", "", text, flags=re.IGNORECASE | re.MULTILINE).strip()
    fields = {}
    for m in re.finditer(r"^\s*([A-Za-z][A-Za-z0-9 _/]+?)\s*:\s*(.+?)\s*$", body, re.MULTILINE):
        key = m.group(1).strip()
        val = m.group(2).strip()
        if 1 <= len(key) <= 40 and val:
            fields[key] = val
    headline = ""
    for line in body.splitlines():
        line = line.strip().strip("-").strip()
        if line and ":" not in line and not set(line) <= set("-_= "):
            headline = line
            break
    return {
        "subject": subject,
        "headline": headline or subject,
        "fields": fields,
        "body": body,
        "info_badge": "ALERT",
    }
 
def render_tc_alert_html(name, data):
    html = '<div class="pd-inline-log"><div class="pd-inline-log-title">{}</div>'.format(
        _escape_html(data.get("subject") or "TC Alert"))
    html += '<div class="tc-alert-box"><div class="tc-alert-headline">{}</div>'.format(
        _escape_html(data.get("headline") or "Alert received"))
    fields = data.get("fields", {})
    if fields:
        html += '<div class="tc-alert-grid">'
        for k, v in fields.items():
            html += '<span class="tc-alert-k">{}</span><span class="tc-alert-v">{}</span>'.format(
                _escape_html(k), _escape_html(v))
        html += "</div>"
    body = data.get("body", "")
    if body and not fields:
        html += '<div class="tc-alert-msg">{}</div>'.format(_escape_html(body[:600]))
    return html + "</div></div>"
 
# --------------------------------------------------------------------------- #
# Bill Reject status (CSV attachment)
# --------------------------------------------------------------------------- #
def parse_reject_status(content, filepath=""):
    text = _tc_prep_text(content, filepath)
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
        if not header and any(h.upper() in ("BA_NO", "CUSTOMER_NO", "RESP_TEAM", "REASON") for h in cells):
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
        "info_badge": _tc_human_num(total),
    }
 
def render_reject_status_html(data):
    html = '<div class="pd-inline-log"><div class="pd-inline-log-title">Bill Reject Monitoring (BE)</div>'
    html += (
        '<div class="pd-inline-log-summary"><span>Total Rejects: {}</span>'
        '<span class="pd-sum-info">Teams: {}</span></div>'
    ).format(data.get("total", 0), len(data.get("teams", [])))
    teams = data.get("teams", [])
    if teams:
        html += '<div class="tc-section"><div class="tc-section-title"><span>By Responsible Team</span></div>'
        html += '<div class="mco-metrics-grid">'
        for name_, count in teams:
            html += '<div class="bap-count-row"><span class="bap-svc-name">{}</span><span class="bap-count-val">{}</span></div>'.format(
                _escape_html(name_ or "—"), count)
        html += "</div></div>"
    reasons = data.get("top_reasons", [])
    if reasons:
        html += '<div class="tc-section"><div class="tc-section-title"><span>Top Reject Reasons</span></div>'
        html += '<div class="tc-table-wrap"><table class="tc-table"><thead><tr><th>Reason</th><th>Count</th></tr></thead><tbody>'
        for reason, count in reasons:
            html += '<tr><td style="white-space:normal">{}</td><td>{}</td></tr>'.format(
                _escape_html(reason), count)
        html += "</tbody></table></div></div>"
    return html + "</div>"
 
def render_tc_report_html(name, data):
    if data is None:
        return '<div class="pd-log-warn">No report data found.</div>'
    if name in TC_ALERT_CHECKS:
        return render_tc_alert_html(name, data)
    if name in TC_REJECT_CHECKS:
        return render_reject_status_html(data)
    if name in TC_SECTIONED_CHECKS:
        return render_sectioned_report_html(name, data)
    return '<div class="pd-log-warn">Unsupported TC report.</div>'
 
st.set_page_config(
    page_title="Production Validation Dashboard",
    page_icon="shield",
    layout="wide",
    initial_sidebar_state="collapsed"
)
st.markdown("""
<style>
html { box-sizing: border-box; }
*, *:before, *:after { box-sizing: inherit; }
[data-testid="stSidebar"],
[data-testid="collapsedControl"] { display: none !important; }
header[data-testid="stHeader"],
.stDeployButton, #MainMenu, footer { display: none !important; }
.stApp, [data-testid="stAppViewContainer"], section.main {
    padding-top: 0 !important;
    margin-top: 0 !important;
}
:root {
    --bg-0: #EEF2F8;
    --bg-1: #E4EAF3;
    --surface: #FFFFFF;
    --surface-2: #F8FAFC;
    --border: #E2E8F0;
    --border-strong: #CBD5E1;
    --ink: #0F172A;
    --ink-2: #334155;
    --muted: #64748B;
    --faint: #94A3B8;
    --brand: #4F46E5;
    --brand-2: #6366F1;
    --ok: #16A34A;
    --ok-bg: #DCFCE7;
    --warn: #D97706;
    --warn-bg: #FEF3C7;
    --fail: #DC2626;
    --fail-bg: #FEE2E2;
    --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.06), 0 1px 3px rgba(15, 23, 42, 0.05);
    --shadow-md: 0 4px 14px rgba(15, 23, 42, 0.08);
    --shadow-lg: 0 12px 32px rgba(15, 23, 42, 0.14);
}
html, body, .stApp {
    background: #F4F6FA !important;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif !important;
    color: var(--ink) !important;
    -webkit-font-smoothing: antialiased;
}
.element-container { margin-bottom: 0 !important; }
.block-container {
    padding-top: 16px !important;
    padding-bottom: 20px !important;
    padding-left: 1.5rem !important;
    padding-right: 1.5rem !important;
    max-width: 1560px !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
}
.hdr-shell {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    margin-bottom: 12px;
    overflow: hidden;
}
.hdr-shell .page-hdr {
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    align-items: center;
    gap: 16px;
    border: none; margin: 0; border-radius: 0;
    border-bottom: 1px solid var(--border);
    padding: 12px 16px;
}
.hdr-shell div[data-testid="stHorizontalBlock"] {
    border: none !important; margin: 0 !important; border-radius: 0 !important;
    background: transparent !important;
    padding: 8px 14px 10px 14px !important;
}
.hdr-shell div[data-testid="stCheckbox"] label span { font-size: 12px !important; }
.hdr-shell div[data-testid="stToggle"] label span { font-size: 12px !important; }
.hdr-shell div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:last-child {
    display: flex !important;
    flex-direction: column !important;
    justify-content: center !important;
    align-items: flex-end !important;
}
.hdr-shell div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:last-child .element-container {
    margin-bottom: 2px !important;
    width: 100%;
}
.hdr-shell div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:last-child .element-container:last-child {
    margin-bottom: 0 !important;
}
.hdr-center { text-align: center; min-width: 0; }
.hdr-spacer { min-width: 0; }
.page-title {
    font-size: 18px;
    font-weight: 700;
    color: var(--ink);
    margin: 0;
    line-height: 1.2;
}
.page-subtitle {
    font-size: 12px;
    font-weight: 400;
    color: var(--muted);
}
.page-meta {
    display: flex; align-items: center; gap: 8px; flex-shrink: 0; flex-wrap: wrap;
    justify-content: flex-end;
    justify-self: end;
}
.mode-pill {
    font-size: 11px; font-weight: 600;
    color: var(--ink-2); background: var(--surface-2);
    border: 1px solid var(--border);
    padding: 5px 10px; border-radius: 6px;
}
.mode-pill.is-live { color: #15803D; background: #F0FDF4; border-color: #BBF7D0; }
.mode-pill.is-hist { color: #B45309; background: #FFFBEB; border-color: #FDE68A; }
.page-ts {
    font-size: 12px;
    font-weight: 500;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
}
.back-link {
    display: inline-block;
    font-size: 12px;
    font-weight: 600;
    color: #4F46E5 !important;
    text-decoration: none !important;
    margin-bottom: 8px;
}
.sb-outer {
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
    margin-bottom: 12px !important;
    background: var(--surface);
}
.sb-grid { display: grid; grid-template-columns: repeat(4, 1fr) 1.35fr; width: 100%; }
.sb-cell {
    padding: 12px 16px; border-right: 1px solid var(--border);
    background: var(--surface); min-width: 0;
}
.sb-cell.sb-pass .sb-cval { color: var(--ok); }
.sb-cell.sb-fail .sb-cval { color: var(--fail); }
.sb-cell.sb-warn .sb-cval { color: var(--warn); }
.sb-click { cursor: pointer; transition: background 0.15s ease; }
.sb-click:hover { background: var(--surface-2); }
.sb-click:active { background: #EEF2F7; }
.sb-last {
    padding: 12px 16px; background: var(--surface-2); min-width: 0; border-right: none;
}
.sb-clbl { font-size: 11px; color: var(--muted); margin-bottom: 4px; font-weight: 500; }
.sb-cval { font-size: 24px; font-weight: 700; line-height: 1; font-variant-numeric: tabular-nums; color: var(--ink); }
.sb-bar { height: 6px; border-radius: 4px; background: #E5E7EB; overflow: hidden; margin-top: 8px; }
.sb-barfg { height: 100%; border-radius: 4px; }
.sb-barlb { font-size: 11px; color: var(--muted); margin-top: 4px; }
.trend-title {
    font-size: 12px;
    font-weight: 600;
    color: var(--muted);
    margin-bottom: 0;
}
[data-testid="stHorizontalBlock"] { gap: 0.75rem !important; align-items: center !important; }
[data-testid="column"] { padding-left: 0 !important; padding-right: 0 !important; min-width: 0 !important; }
.prod-unified-card {
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--surface);
    overflow: hidden;
    height: 100%;
    display: flex;
    flex-direction: column;
    margin-bottom: 0;
}
.prod-unified-card.accent-fail { border-left: 3px solid var(--fail); }
.prod-unified-card.accent-warn { border-left: 3px solid var(--warn); }
.prod-unified-card.accent-ok   { border-left: 3px solid var(--ok); }
.prod-link { text-decoration: none !important; color: inherit !important; display: block; }
.prod-card-top {
    padding: 10px 12px 8px 12px;
    cursor: pointer;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
}
.prod-link:hover .prod-card-top { background: var(--surface-2); }
.prod-open-icon { display: none; }
.prod-summary-top {
    display: flex; justify-content: space-between; align-items: center;
    gap: 8px; margin-bottom: 2px;
}
.prod-summary-nm { font-size: 14px; font-weight: 700; color: var(--ink); }
.prod-summary-sub { font-size: 11px; color: var(--muted); }
.prod-grid-st { font-size: 10px; font-weight: 600; padding: 2px 7px; border-radius: 4px; white-space: nowrap; }
.st-ok   { background: var(--ok-bg); color: #15803D; }
.st-warn { background: var(--warn-bg); color: var(--warn); }
.st-fail { background: var(--fail-bg); color: var(--fail); }
.prod-card-chips {
    display: flex; flex-wrap: wrap; gap: 4px;
    padding: 8px 10px 10px 10px; background: var(--surface); flex: 1;
    align-content: flex-start;
}
.cchip {
    font-size: 9px; font-weight: 600; line-height: 1.3;
    padding: 2px 6px; border-radius: 4px; white-space: nowrap;
    border: 1px solid transparent;
}
.cchip-pass { background: #F0FDF4; color: #15803D; border-color: #DCFCE7; }
.cchip-info { background: #EFF6FF; color: #1D4ED8; border-color: #DBEAFE; }
.cchip-warn { background: #FEF3C7; color: #B45309; border-color: #FDE68A; }
.cchip-fail { background: #FEE2E2; color: #DC2626; border-color: #FECACA; font-weight: 700; }
.filter-strip {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 10px 14px;
    margin-bottom: 12px;
}
.flt-bar {
    display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
}
.flt-lead {
    font-size: 12px; font-weight: 600; color: var(--muted); margin-right: 6px;
}
.flt-btn {
    display: inline-flex; align-items: center; gap: 5px;
    font-size: 12px; font-weight: 500; color: var(--ink-2);
    background: var(--surface-2); border: 1px solid var(--border);
    padding: 4px 10px; border-radius: 6px; cursor: pointer; user-select: none;
}
.flt-btn:hover { border-color: var(--border-strong); background: #fff; }
.flt-btn .flt-n {
    font-size: 11px; font-weight: 600; padding: 0 5px; border-radius: 4px;
    background: rgba(100, 116, 139, 0.12); min-width: 16px; text-align: center;
}
.flt-btn.flt-on { background: var(--ink); color: #FFF; border-color: var(--ink); }
.flt-btn.flt-on .flt-n { background: rgba(255, 255, 255, 0.2); }
.search-inp {
    flex: 1 1 160px; min-width: 140px; max-width: 240px;
    font-size: 12px; padding: 5px 10px; border-radius: 6px;
    border: 1px solid var(--border); background: var(--surface-2);
    color: var(--ink); outline: none;
}
.search-inp:focus { border-color: var(--border-strong); background: #fff; }
.search-inp::placeholder { color: var(--faint); }
.cchip.search-hide { display: none !important; }
.prod-unified-card.search-hide-card { display: none !important; }
.home-wrap[data-flt="fail"] .cchip:not(.cchip-fail),
.home-wrap[data-flt="warn"] .cchip:not(.cchip-warn),
.home-wrap[data-flt="info"] .cchip:not(.cchip-info),
.home-wrap[data-flt="pass"] .cchip:not(.cchip-pass) { display: none; }
.home-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 10px; align-items: start;
}
.home-empty {
    display: none; grid-column: 1 / -1; text-align: center; padding: 24px 10px;
    color: var(--muted); font-size: 13px;
}
.sb-click.flt-on { background: var(--surface-2); box-shadow: inset 0 0 0 2px var(--border-strong); }
@media (max-width: 1200px) { .home-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 860px)  { .home-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.issues-link { text-align: right; margin-top: 10px; }
.issues-link a {
    color: var(--fail); font-weight: 600; font-size: 12px; text-decoration: none;
}
div[data-testid="stToggle"] { margin: 0; padding-top: 2px; }
div[data-testid="stToggle"] label span { font-size: 13px !important; font-weight: 600 !important; }
div[data-testid="stDateTimeInput"] { margin: 0; padding: 0; }
div[data-testid="stDateTimeInput"] label p {
    font-size: 11px !important; font-weight: 500 !important;
    color: var(--muted) !important; margin-bottom: 2px !important;
}
div[data-testid="stDateTimeInput"] input { font-size: 13px !important; border-radius: 6px !important; }
/* ── Product detail page card ── */
.prod-detail-card {
    border: 1px solid #CBD5E1;
    border-radius: 12px;
    background: #FFFFFF;
    overflow: hidden;
    box-shadow: 0 3px 12px rgba(15, 23, 42, 0.07);
    margin-bottom: 12px;
}
.prod-detail-card.accent-fail { border-top: 4px solid #DC2626; }
.prod-detail-card.accent-warn { border-top: 4px solid #D97706; }
.prod-detail-card.accent-ok   { border-top: 4px solid #16A34A; }
.pd-card-top {
    padding: 12px 14px 10px 14px;
    border-bottom: 1px dashed #E2E8F0;
    background: linear-gradient(135deg, #FAFAFA 0%, #F1F5F9 100%);
}
.pd-card-summary-top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    margin-bottom: 3px;
}
.pd-card-nm {
    font-size: 17px;
    font-weight: 800;
    color: #0F172A;
}
.pd-card-sub {
    font-size: 11px;
    color: #64748B;
    font-weight: 500;
}
.pd-card-body {
    padding: 4px 0;
    background: #EEF2F7;
}
.pd-row-wrap {
    border-bottom: 1px solid #EEF2F7;
    background: #FFFFFF;
}
.pd-row-wrap:last-child { border-bottom: none; }
.pd-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 14px;
    min-height: 28px;
}
.pd-row.row-fail { background: #FFFCFC; }
.pd-row.row-warn { background: #FFFEF9; }
.pd-row-name {
    flex: 1;
    font-size: 11px;
    font-weight: 600;
    color: #334155;
    min-width: 0;
    line-height: 1.25;
}
.pd-row.row-fail .pd-row-name { color: #991B1B; font-weight: 700; }
.pd-row.row-warn .pd-row-name { color: #92400E; font-weight: 700; }
.pd-row.row-pass .pd-row-name { color: #64748B; font-weight: 500; }
.pd-row-age {
    font-size: 10px;
    color: #94A3B8;
    white-space: nowrap;
    flex-shrink: 0;
}
.pd-row-badge {
    font-size: 8px;
    font-weight: 700;
    padding: 2px 7px;
    border-radius: 4px;
    white-space: nowrap;
    flex-shrink: 0;
    min-width: 34px;
    text-align: center;
}
/* Descriptive sub-line under each detail row */
.pd-row-sub {
    padding: 0 14px 8px 14px;
    margin-top: -3px;
    line-height: 1.35;
}
.pd-row-desc {
    font-size: 10.5px;
    color: var(--muted);
    font-weight: 500;
}
.pd-row-reason {
    display: block;
    font-size: 10.5px;
    font-weight: 700;
    margin-top: 1px;
}
.reason-fail { color: var(--fail); }
.reason-warn { color: var(--warn); }
.reason-pass { color: var(--ok); }
.pd-row.row-pass .pd-row-badge { background: #DCFCE7; color: #15803D; }
.pd-row.row-fail .pd-row-badge { background: #FEE2E2; color: #DC2626; }
.pd-row.row-warn .pd-row-badge { background: #FEF3C7; color: #D97706; }
a.pd-log-toggle {
    color: #64748B !important;
    text-decoration: none !important;
    font-size: 15px !important;
    font-weight: 400 !important;
    padding: 0 2px !important;
    background: none !important;
    border: none !important;
    box-shadow: none !important;
    line-height: 1 !important;
    flex-shrink: 0;
}
a.pd-log-toggle:hover { color: #374151 !important; }
/* Expanded row + logs in one highlighted box */
.pd-row-wrap.pd-row-open {
    margin: 8px 10px;
    border: 2px solid #93C5FD;
    border-radius: 8px;
    background: #FEF3C7;
    box-shadow: 0 2px 8px rgba(59, 130, 246, 0.15);
    overflow: hidden;
    border-bottom: 2px solid #93C5FD;
}
.pd-row-wrap.pd-row-open.pd-expand-fail {
    border-color: #334155;
    background: #FFFBFB;
    box-shadow: 0 2px 8px rgba(220, 38, 38, 0.18);
}
.pd-row-wrap.pd-row-open.pd-expand-warn {
    border-color: #FBBF24;
    background: #FEF3C7;
    box-shadow: 0 2px 8px rgba(217, 119, 6, 0.10);
}
.pd-row-wrap.pd-row-open.pd-expand-pass {
    border-color: #BBF7D0;
    background: #F7FEF9;
    box-shadow: 0 2px 6px rgba(22, 163, 74, 0.10);
}
.pd-row-wrap.pd-row-open .pd-row {
    background: transparent !important;
    border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}
.pd-log-panel {
    background: transparent;
    border-top: 1px solid rgba(0, 0, 0, 0.06);
    padding: 8px 12px 10px 12px;
}
.pd-row-wrap.pd-row-open.pd-expand-fail .pd-log-panel {
    border-top-color: #FECACA;
}
.pd-row-wrap.pd-row-open.pd-expand-warn .pd-log-panel {
    border-top-color: #FDE68A;
}
.pd-row-wrap.pd-row-open.pd-expand-pass .pd-log-panel {
    border-top-color: #BBF7D0;
}
.pd-inline-log-title {
    font-size: 10px;
    font-weight: 700;
    color: #374151;
    text-transform: uppercase;
    margin: 2px 0 4px;
}
.pd-inline-log-summary {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    margin-bottom: 4px;
    font-size: 10px;
    font-weight: 700;
}
.pd-sum-ok {
    background: #DCFCE7;
    color: #15803D;
    font-size: 9px;
    font-weight: 700;
    padding: 1px 6px;
    border-radius: 4px;
}
.pd-sum-fail {
    background: #FEE2E2;
    color: #DC2626;
    font-size: 9px;
    font-weight: 700;
    padding: 1px 6px;
    border-radius: 4px;
}
.pd-sum-warn {
    background: #FEF3C7;
    color: #D97706;
    font-size: 9px;
    font-weight: 700;
    padding: 1px 6px;
    border-radius: 4px;
}
.pd-grid-2col {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 4px;
}
.pd-grid-item {
    margin-bottom: 2px;
}
.pd-log-warn {
    font-size: 11px;
    color: #D97706;
    background: #FFFBEB;
    border: 1px solid #FDE68A;
    border-radius: 4px;
    padding: 6px 8px;
}
.pd-raw-log {
    font-size: 9px;
    color: #374151;
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 4px;
    padding: 6px 8px;
    max-height: 120px;
    overflow: auto;
    white-space: pre-wrap;
    margin: 0;
}
/* ── Log grid items ── */
.daemon-row {
    display: flex; align-items: center; gap: 5px;
    padding: 4px 6px; border-radius: 4px;
    border: 1px solid #E2E8F0; background: #FFFFFF; width: 100%;
}
.daemon-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.daemon-nm { font-size: 10px; font-weight: 600; color: #1F2937; flex: 1; min-width: 0; }
.daemon-st { font-size: 8px; font-weight: 700; padding: 1px 5px; border-radius: 3px; }
.d-up   { background: #DCFCE7; color: #15803D; }
.d-down { background: #FEE2E2; color: #DC2626; }
.d-warn { background: #FEF3C7; color: #D97706; }
.jvm-exc { font-size: 8px; color: #6B7280; padding: 0 6px 2px 14px; }
/* ── Sub-component group headers ── */
.chk-group-hdr {
    font-size: 8px; font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase;
    color: #6366F1; background: #EEF0FE; padding: 3px 8px; border-radius: 5px;
    margin: 8px 0 4px; border-left: 3px solid #A5B4FC;
}
.chk-group-hdr:first-child { margin-top: 0; }
.pd-group-hdr {
    font-size: 10px; font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase;
    color: #4338CA; background: linear-gradient(90deg, #EEF0FE 0%, #F8FAFC 100%);
    padding: 7px 16px; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border);
    border-left: 4px solid #818CF8;
}
.pd-group-hdr:first-child { border-top: none; }
/* ── Modal (CSS :target based, no rerun / no new tab) ── */
.modal-overlay {
    display: none; position: fixed; inset: 0;
    background: rgba(15, 23, 42, 0.55);
    backdrop-filter: blur(4px); -webkit-backdrop-filter: blur(4px);
    z-index: 9999; align-items: flex-start; justify-content: center;
    padding: 46px 16px; overflow-y: auto;
}
.modal-overlay:target { display: flex; }
.modal-box {
    background: var(--surface); border-radius: 16px; max-width: 940px; width: 100%;
    box-shadow: 0 30px 70px rgba(0, 0, 0, 0.4);
    position: relative; max-height: 88vh; overflow-y: auto;
    border: 1px solid rgba(255, 255, 255, 0.6);
    animation: popIn 0.28s cubic-bezier(0.22, 1, 0.36, 1) both;
}
.modal-hdr {
    position: sticky; top: 0; z-index: 5;
    display: flex; justify-content: space-between; align-items: center;
    padding: 15px 20px;
    background: linear-gradient(100deg, #0F172A 0%, #1E293B 55%, #312E81 150%);
    border-radius: 16px 16px 0 0;
}
.modal-title { font-size: 17px; font-weight: 800; color: #F8FAFC; letter-spacing: -0.01em; }
.modal-sub { font-size: 11px; font-weight: 600; color: #A5B4FC; margin-left: 10px; }
.modal-close {
    color: #F8FAFC !important; text-decoration: none !important;
    font-size: 20px; font-weight: 700; line-height: 1; flex-shrink: 0;
    width: 32px; height: 32px; display: flex; align-items: center; justify-content: center;
    border-radius: 9px; background: rgba(255, 255, 255, 0.12); transition: background 0.18s ease;
}
.modal-close:hover { background: rgba(239, 68, 68, 0.5); }
.modal-body { padding: 4px 0 14px; }
/* ── details-based inline log toggle (client-side, no rerun) ── */
details.pd-row-det { border-bottom: 1px solid #EEF2F7; background: #FFFFFF; }
details.pd-row-det:last-child { border-bottom: none; }
details.pd-row-det > summary {
    list-style: none; cursor: pointer; outline: none;
}
details.pd-row-det > summary::-webkit-details-marker { display: none; }
details.pd-row-det > summary::marker { content: ""; }
details.pd-row-det .pd-log-toggle {
    color: #94A3B8; font-size: 12px; transition: transform 0.15s ease;
}
details.pd-row-det[open] > summary .pd-log-toggle { transform: rotate(90deg); color: #374151; }
details.pd-row-det[open] {
    margin: 8px 10px; border: 2px solid #93C5FD; border-radius: 8px;
    background: #FEF3C7; box-shadow: 0 2px 8px rgba(59, 130, 246, 0.15); overflow: hidden;
}
details.pd-row-det[open].det-fail { border-color: #334155; background: #FFFBFB; box-shadow: 0 2px 8px rgba(220, 38, 38, 0.18); }
details.pd-row-det[open].det-warn { border-color: #FBBF24; background: #FEF3C7; box-shadow: 0 2px 8px rgba(217, 119, 6, 0.10); }
details.pd-row-det[open].det-pass { border-color: #BBF7D0; background: #F7FEF9; box-shadow: 0 2px 6px rgba(22, 163, 74, 0.10); }
details.pd-row-det[open].det-info { border-color: #93C5FD; background: #EFF6FF; box-shadow: 0 2px 8px rgba(37, 99, 235, 0.12); }
details.pd-row-det[open] > summary .pd-row { background: transparent !important; }
""" + METRIC_REPORT_CSS + """
</style>
""", unsafe_allow_html=True)
REFRESH_INTERVAL = 60
JVM_CHECKS = {
    "ABP_jvm_check", "CRM_jvm_check", "OMS_jvm_check", "MCSS_jvm_check",
    "OMNI_jvm_check", "WSF_jvm_check", "ASOM_jvm_check",
    "MCO_jvm_check",
}
JVM_SHORT_NAMES = {
    "ABP_jvm_check":  "ABP JVM",
    "CRM_jvm_check":  "CRM JVM",
    "OMNI_jvm_check": "OMNI JVM",
    "MCO_jvm_check":  "MCO JVM",
    "WSF_jvm_check":  "WSF JVM",
    "OMS_jvm_check":  "OMS JVM",
    "MCSS_jvm_check": "MCSS JVM",
    "ASOM_jvm_check": "ASOM JVM",
    "JVM_Health":     "WebLogic",
}
BILLING_CHECKS = {
    "BTLSOR_Monitoring_Report",
    "Collection_Letters_Monitoring_Report",
    "Reprint_Health_Checkup",
    "RECEIPT_FAILURE_DETAILS_REPORT_SUBSCRIPTION",
    "RECEIPT_FAILURE_DETAILS_REPORT_UPFORNT",
    "BACKDATE_FAILURE_REPORT",
    "PENDING_REBILL_FILES_REPORT",
    "TC_DUPLICATE_MAP_REPORT",
}
BILLING_DISPLAY_NAMES = {
    "BTLSOR_Monitoring_Report":                    "BTLSOR",
    "Collection_Letters_Monitoring_Report":        "Coll Letters",
    "Reprint_Health_Checkup":                      "Reprint",
    "RECEIPT_FAILURE_DETAILS_REPORT_SUBSCRIPTION": "Sub Receipt",
    "RECEIPT_FAILURE_DETAILS_REPORT_UPFORNT":      "Upfront Rcpt",
    "BACKDATE_FAILURE_REPORT":                     "Backdate",
    "PENDING_REBILL_FILES_REPORT":                 "Rebill",
    "TC_DUPLICATE_MAP_REPORT":                     "Dup Map",
}
DASHBOARD_NAMES = {
    "Daemon_Health": "Daemon Health",
    "CM9FUTREQ_Failed_Record_Alert": "CM9 Future",
    "AR3GWLSTNR_Daemon_Failed_File_Alert": "GWLS File",
    "Feedback_and_Payment_Files_pending_for_processing_Alert": "Feedback/Pymt",
    "AR9PYMRCTUPD_Daemon_Processing_Alert_Pending_Records": "AR9 Pending",
    "URGENT_ALERT_Subscription_Accounts_Pay_Means_Mismatch": "Pay Means",
    "Subscription_Accounts_DD_Payment_Method": "DD Payment",
    "Collection_Health_Check_Report": "Collection",
    "TRB1_SUB_ERRS_Alert": "TRB1 Errors",
    "BAP_Report": "BAP Report",
    "Script_Monitoring_Report": "Script Mon",
    "MCO_Server": "MCO Server",
    "AC1_CONTROL_PROBLEMATIC_FILES_ALERT": "AC1 Prob",
    "AC1_CONTROL_PROBLEMATIC_EP_FILES_ALERT": "AC1 EP",
    "AC1_MANAGER_THREAD_ALERT": "AC1 Thread",
    "ELA_STATS_ALERT": "ELA Stats",
    "IMDG_GRID_NTF_COUNT_ALERT": "IMDG Grid",
    "REJECTED_USAGE_ALERT": "Rejected Usage",
    "RTN_DASHBOARD_IMDG_ALERT": "RTN Dashboard",
    "UNPROCESSED_COUNT_ALERT_For_EnvFix_ManFix_ReRun": "Unprocessed",
    "UQ_MONITORING_ALRET": "UQ Monitor",
    "USAGE_BACKLOG_TPS_ONLINE_OFFLINE_ALERT": "Usage Backlog",
    "OMS_Collection_Report": "OMS Collection",
    "ASOM_Server_Request_Monitoring": "ASOM Server",
    "CRM_OBJID_Sequence_Report": "CRM OBJID",
    "ASOM_OBJID_Sequence_Report": "ASOM OBJID",
    "Promotion_Expiry_Forecast": "Promo Expiry",
    "SR_rate_Prepaid_postpaid_completion_response": "SR Rate",
    "ANM_Health_Check_Report": "ANM Health",
}
DASHBOARD_NAMES.update(BILLING_DISPLAY_NAMES)
DASHBOARD_NAMES.update(JVM_SHORT_NAMES)
DASHBOARD_NAMES.update(_MR_DASHBOARD_NAMES)
HTML_REPORT_CHECKS = {"Daemon_Health", "JVM_Health"}
# Sub-component (application / system) grouping for each check within a product.
CHECK_GROUPS = {
    "ABP": {
        "Daemon_Health":                                           "System Health",
        "ABP_jvm_check":                                           "ABP JVM",
        "CM9FUTREQ_Failed_Record_Alert":                          "AR Daemons & Alerts",
        "AR3GWLSTNR_Daemon_Failed_File_Alert":                    "AR Daemons & Alerts",
        "Feedback_and_Payment_Files_pending_for_processing_Alert": "AR Daemons & Alerts",
        "AR9PYMRCTUPD_Daemon_Processing_Alert_Pending_Records":   "AR Daemons & Alerts",
        "URGENT_ALERT_Subscription_Accounts_Pay_Means_Mismatch":  "AR Daemons & Alerts",
        "Subscription_Accounts_DD_Payment_Method":                "AR Daemons & Alerts",
        "Collection_Health_Check_Report":                         "AR Daemons & Alerts",
        "TRB1_SUB_ERRS_Alert":                                    "AR Daemons & Alerts",
        "CL_Collection_No_Request_CM_to_OMS":                     "CL Reports",
        "CL_BCC_Mercantile_TDX_File_Report":                      "CL Reports",
        "CL_Collection_Activities_Report":                        "CL Reports",
        "CL_Collection_Letters_Monitoring":                       "CL Reports",
        "CL_Health_Check_Report":                                 "CL Reports",
        "CL_Collection_Staggering_Backlog":                       "CL Reports",
        "CL_Missing_TDX_Transaction":                             "CL Reports",
        "CL_Receipt_Failure_Monitor":                             "CL Reports",
        "CM_User_Groups_Mismatch":                                "CM Reports",
        "CM_Customer_Type_Mismatch":                              "CM Reports",
        "CM_PCN_BAN_BEN_Status_Mismatch":                         "CM Reports",
        "AR_AC1_Control_Problematic_Files":                       "AR Email Reports",
        "AR_BL_Mismatch":                                         "AR Email Reports",
        "AR_CL_Bucket_Mismatch":                                  "AR Email Reports",
        "AR3GWLSTNR_Daemon_Failed_File_Urgent":                   "AR Email Reports",
        "AR3GWLSTR_File_Processing":                              "AR Email Reports",
        "AR9PYMRCTUPD_Daemon_Processing_Alert":                     "AR Email Reports",
        "AR_ATB_GL_Recon":                                        "AR Email Reports",
        "AR_Accounts_Stuck_Trial_Period":                         "AR Email Reports",
        "AR_DD_Validation_Missing_Entries":                       "AR Email Reports",
        "AR_FAILURE_Entries_Missing":                             "AR Email Reports",
        "AR_BCC_Missing_Subscription_DD_Rejection":                "AR Email Reports",
        "AR_Cost_Center_Change_Report":                           "AR Email Reports",
        "AR_DSPREJ_Not_Journalized":                              "AR Email Reports",
        "AR_Feedback_Payment_Files_Pending":                      "AR Email Reports",
        "AR_IC289_Variance":                                      "AR Email Reports",
        "AR_Nameline1_Null_Name_Data":                            "AR Email Reports",
        "AR_Missing_Invoices_AR_GL":                              "AR Email Reports",
        "AR_Subscription_Pay_Means_Mismatch_Backend":             "AR Email Reports",
        "AR3GWLSTR_Daemon_Stuck":                                 "AR Email Reports",
        "AR_DDFeedback_Payment_Errored_Files":                    "AR Email Reports",
        "AR_WriteOff_Exited_Collection_After_Payment":            "AR Email Reports",
    },
    "Billing": {
        "BTLSOR_Monitoring_Report":                    "SOR & Batch",
        "BACKDATE_FAILURE_REPORT":                     "SOR & Batch",
        "PENDING_REBILL_FILES_REPORT":                 "SOR & Batch",
        "TC_DUPLICATE_MAP_REPORT":                     "SOR & Batch",
        "Collection_Letters_Monitoring_Report":        "Letters & Reprint",
        "Reprint_Health_Checkup":                      "Letters & Reprint",
        "RECEIPT_FAILURE_DETAILS_REPORT_SUBSCRIPTION": "Receipts",
        "RECEIPT_FAILURE_DETAILS_REPORT_UPFORNT":      "Receipts",
    },
    "TC": {
        "AC1_CONTROL_PROBLEMATIC_FILES_ALERT":             "AC1 Control",
        "AC1_CONTROL_PROBLEMATIC_EP_FILES_ALERT":          "AC1 Control",
        "AC1_MANAGER_THREAD_ALERT":                        "AC1 Control",
        "ELA_STATS_ALERT":                                 "Usage & Stats",
        "REJECTED_USAGE_ALERT":                            "Usage & Stats",
        "USAGE_BACKLOG_TPS_ONLINE_OFFLINE_ALERT":          "Usage & Stats",
        "IMDG_GRID_NTF_COUNT_ALERT":                       "IMDG & RTN",
        "RTN_DASHBOARD_IMDG_ALERT":                        "IMDG & RTN",
        "UNPROCESSED_COUNT_ALERT_For_EnvFix_ManFix_ReRun": "Monitoring",
        "UQ_MONITORING_ALRET":                             "Monitoring",
        "TC_Health_Check":                                 "Health & Sanity",
        "AC1_Control_Problematic_Files":                   "AC1 Control",
        "Rerate_Backlog_Status":                           "Backlogs",
        "TC_Bill_Reject_Status":                           "Reports & Alerts",
        "TC_Process_Crash":                                "Alerts",
        "TC_Usage_Backlog_Alert":                          "Alerts",
        "TC_Thread_Control_Down":                          "Alerts",
        "AVM1_ES_Alerts":                                  "Alerts",
        "File_System_Usage_Report":                        "Infrastructure",
    },
    "CRM": {
        "CRM_jvm_check":             "JVM Exceptions",
        "BAP_Report":                "Reports & Alerts",
        "BAP_Error_Report":          "Reports & Alerts",
        "Script_Monitoring_Report":  "Reports & Alerts",
        "CRM_OBJID_Sequence_Report": "Reports & Alerts",
    },
    "Digital": {
        "MCSS_jvm_check": "MCSS",
        "OMNI_jvm_check": "OMNI",
        "BDN_BRN_Report": "Bill Notifications",
    },
    "OMS": {
        "OMS_jvm_check":                                "JVM Exceptions",
        "OMS_Collection_Report":                        "Reports & Alerts",
        "SR_rate_Prepaid_postpaid_completion_response": "Reports & Alerts",
    },
    "ASOM": {
        "ASOM_jvm_check":                 "JVM Exceptions",
        "ASOM_Server_Request_Monitoring": "Reports & Alerts",
        "ASOM_OBJID_Sequence_Report":     "Reports & Alerts",
        "JVM_Threads_Report":             "JVM Monitoring",
    },
    "MCO": {
        "MCO_jvm_check": "JVM Exceptions",
        "MCO_Server":    "Reports & Alerts",
        "MCO_System_Files_Cleanup": "Reports & Alerts",
    },
    "WSF": {
        "WSF_jvm_check": "JVM Exceptions",
    },
    "System": {
        "JVM_Health":                "Health Checks",
        "ANM_Health_Check_Report":   "Health Checks",
        "Promotion_Expiry_Forecast": "Forecasts",
        "File_System_Usage_Report":  "Infrastructure",
    },
}
 
def check_group(product, name):
    return CHECK_GROUPS.get(product, {}).get(name, "Other")
 
def group_results(product):
    """Return [(group_name, [(check_name, result), ...]), ...] ordered by worst status."""
    order = {"FAIL": 0, "WARNING": 1, "PASS": 2}
    groups = {}
    for name in CHECKS[product]:
        r = results[product][name]
        groups.setdefault(check_group(product, name), []).append((name, r))
    for g in groups:
        groups[g].sort(key=lambda x: order.get(x[1]["status"], 3))
    def group_rank(items):
        if any(r["status"] == "FAIL" for _, r in items):
            return 0
        if any(r["status"] == "WARNING" for _, r in items):
            return 1
        return 2
    return sorted(groups.items(), key=lambda kv: group_rank(kv[1]))
BASE = os.path.join(
    os.environ.get("VALIDATION_HOME", os.path.dirname(os.path.abspath(__file__))),
    "validation",
)
_METRIC_CHECKS = dict(check_entries(BASE))
for _name, _cfg in {
    "BAP_Error_Report": {
        "pattern": BASE + "/bap/BAP_Error_Report*",
        "keyword": "",
        "desc": "BAP Pending Records by Service",
    },
    "MCO_System_Files_Cleanup": {
        "pattern": BASE + "/mco/MCO_System_Files_Cleanup*",
        "keyword": "",
        "desc": "MCO System Files Cleanup Execution",
    },
    "File_System_Usage_Report": {
        "pattern": BASE + "/filesystem/PROD_FILE_SYSTEM*",
        "keyword": "",
        "desc": "Production File System Usage Report",
    },
    "JVM_Threads_Report": {
        "pattern": BASE + "/jvm_threads/JVM_Threads_Report*",
        "keyword": "",
        "desc": "OPTUS JVM Threads Report",
    },
}.items():
    _METRIC_CHECKS.setdefault(_name, _cfg)
CHECKS = {
    "ABP": {
        "Daemon_Health":                                             {"pattern": BASE+"/healthcheck/SysBounceReport*.html",                           "keyword":"DOWN",      "desc":"System Daemon Bounce Report"},
        "ABP_jvm_check":                                             {"pattern": BASE+"/abp_exception/ABP_logcheck.log.*",                            "keyword":"Exception", "desc":"ABP JVM Exception Monitor"},
        "CM9FUTREQ_Failed_Record_Alert":                             {"pattern": BASE+"/abp/CM9FUTREQ_Failed_Record_Alert/cm9_future_req.log*",       "keyword":"NOT_OK",    "desc":"CM9 Future Request Failed Records"},
        "AR3GWLSTNR_Daemon_Failed_File_Alert":                       {"pattern": BASE+"/abp/gatewaylist_failed_file_alert/gwls_file.log*",            "keyword":"NOT_OK",    "desc":"AR3GWLSTNR Daemon Failed File Alert"},
        "Feedback_and_Payment_Files_pending_for_processing_Alert":   {"pattern": BASE+"/abp/feedback_pymt_file_alert/fd_pymt_pnd.log*",              "keyword":"NOT_OK",    "desc":"Feedback & Payment Files Pending"},
        "AR9PYMRCTUPD_Daemon_Processing_Alert_Pending_Records":      {"pattern": BASE+"/abp/ar9pymrctupd_alert/ar9pymrctupd_alert*",                 "keyword":"NOT_OK",    "desc":"AR9 Daemon Processing Pending Records"},
        "URGENT_ALERT_Subscription_Accounts_Pay_Means_Mismatch":     {"pattern": BASE+"/abp/subs_pay_means_mismatch_alert/subs_pay_means_mismatch*", "keyword":"NOT_OK",    "desc":"Subscription Pay Means Mismatch"},
        "Subscription_Accounts_DD_Payment_Method":                   {"pattern": BASE+"/abp/subs_dd_pay_means_alert/subs_dd_pay_means*",             "keyword":"NOT_OK",    "desc":"Subscription DD Payment Method"},
        "Collection_Health_Check_Report":                            {"pattern": BASE+"/abp/cl_health_check/cl_health_*",                            "keyword":"NOT_OK",    "desc":"Collection Health Check Report"},
        "TRB1_SUB_ERRS_Alert":                                       {"pattern": BASE+"/abp/trb1_error_status/trb_error_status_*",                   "keyword":"NOT_OK",    "desc":"TRB1 Subscription Error Status"},
        "CL_Collection_No_Request_CM_to_OMS":                        {"pattern": BASE+"/abp/cl_reports/CL_Collection_No_Request*.msg",               "keyword":"",          "desc":"Collection Report — No request from CM to OMS"},
        "CL_BCC_Mercantile_TDX_File_Report":                         {"pattern": BASE+"/abp/cl_reports/CL_BCC_Mercantile_TDX*.msg",                  "keyword":"",          "desc":"BCC CL Mercantile TDX File Report"},
        "CL_Collection_Activities_Report":                           {"pattern": BASE+"/abp/cl_reports/CL_Collection_Activities*.msg",               "keyword":"",          "desc":"Collection Activities Report"},
        "CL_Collection_Letters_Monitoring":                          {"pattern": BASE+"/abp/cl_reports/CL_Collection_Letters*.msg",                  "keyword":"",          "desc":"Collection Letters Monitoring Report"},
        "CL_Health_Check_Report":                                    {"pattern": BASE+"/abp/cl_reports/CL_Health_Check*.msg",                        "keyword":"",          "desc":"CL Health Check Report"},
        "CL_Collection_Staggering_Backlog":                          {"pattern": BASE+"/abp/cl_reports/CL_Staggering_Backlog*.msg",                  "keyword":"",          "desc":"Collection Staggering Backlog Report"},
        "CL_Missing_TDX_Transaction":                                {"pattern": BASE+"/abp/cl_reports/CL_Missing_TDX*.msg",                        "keyword":"",          "desc":"Collection Missing TDX Transaction Report"},
        "CL_Receipt_Failure_Monitor":                                {"pattern": BASE+"/abp/cl_reports/CL_Receipt_Failure*.msg",                     "keyword":"",          "desc":"Receipt Failure Monitor"},
        "CM_User_Groups_Mismatch":                                   {"pattern": BASE+"/abp/cm_reports/CM_User_Groups_Mismatch*.msg",                "keyword":"",          "desc":"CM User Groups Mismatch"},
        "CM_Customer_Type_Mismatch":                                 {"pattern": BASE+"/abp/cm_reports/CM_Customer_Type_Mismatch*.msg",              "keyword":"",          "desc":"Customer Type Mismatch Report"},
        "CM_PCN_BAN_BEN_Status_Mismatch":                           {"pattern": BASE+"/abp/cm_reports/CM_PCN_BAN_BEN_Mismatch*.msg",                "keyword":"",          "desc":"PCN BAN BEN Status Mismatch"},
        "AR_AC1_Control_Problematic_Files":                          {"pattern": BASE+"/abp/ar_reports/AR_AC1_Problematic_Files*.msg",               "keyword":"",          "desc":"AC1 Control Problematic Files"},
        "AR_BL_Mismatch":                                            {"pattern": BASE+"/abp/ar_reports/AR_BL_Mismatch*.msg",                         "keyword":"",          "desc":"AR-BL Mismatch Report"},
        "AR_CL_Bucket_Mismatch":                                     {"pattern": BASE+"/abp/ar_reports/AR_CL_Bucket_Mismatch*.msg",                  "keyword":"",          "desc":"AR-CL Bucket Mismatch Report"},
        "AR3GWLSTNR_Daemon_Failed_File_Urgent":                      {"pattern": BASE+"/abp/ar_reports/AR3GWLSTNR_Failed_File*.msg",                 "keyword":"",          "desc":"AR3GWLSTNR Daemon Failed File (URGENT)"},
        "AR3GWLSTR_File_Processing":                                 {"pattern": BASE+"/abp/ar_reports/AR3GWLSTR_Processing*.msg",                   "keyword":"",          "desc":"AR3GWLSTR File Processing Alert"},
        "AR9PYMRCTUPD_Daemon_Processing_Alert":                      {"pattern": BASE+"/abp/ar_reports/AR9PYMRCTUPD_Alert*.msg",                     "keyword":"",          "desc":"AR9PYMRCTUPD Daemon Processing Alert"},
        "AR_ATB_GL_Recon":                                           {"pattern": BASE+"/abp/ar_reports/AR_ATB_GL_Recon*.msg",                        "keyword":"",          "desc":"ATB GL Reconciliation Report"},
        "AR_Accounts_Stuck_Trial_Period":                            {"pattern": BASE+"/abp/ar_reports/AR_Trial_Period_Stuck*.msg",                  "keyword":"",          "desc":"Accounts Stuck in Trial Period"},
        "AR_DD_Validation_Missing_Entries":                          {"pattern": BASE+"/abp/ar_reports/AR_DD_Val_Missing*.msg",                      "keyword":"",          "desc":"DD Validation — Missing Entries"},
        "AR_FAILURE_Entries_Missing":                                {"pattern": BASE+"/abp/ar_reports/AR_FAILURE_Entries_Missing*.msg",             "keyword":"",          "desc":"FAILURE Entries Missing Alert"},
        "AR_BCC_Missing_Subscription_DD_Rejection":                  {"pattern": BASE+"/abp/ar_reports/AR_BCC_DD_Rejection*.msg",                    "keyword":"",          "desc":"BCC Missing Subscription DD Rejection Notifications"},
        "AR_Cost_Center_Change_Report":                              {"pattern": BASE+"/abp/ar_reports/AR_Cost_Center_Change*.msg",                   "keyword":"",          "desc":"Cost Center Change Report (Master vs Day)"},
        "AR_DSPREJ_Not_Journalized":                                 {"pattern": BASE+"/abp/ar_reports/AR_DSPREJ_Not_Journalized*.msg",                "keyword":"",          "desc":"DSPREJ Not Journalized (URGENT)"},
        "AR_Feedback_Payment_Files_Pending":                         {"pattern": BASE+"/abp/ar_reports/AR_Feedback_Payment_Pending*.msg",            "keyword":"",          "desc":"Feedback & Payment Files Pending for Processing"},
        "AR_IC289_Variance":                                         {"pattern": BASE+"/abp/ar_reports/AR_IC289_Variance*.msg",                      "keyword":"",          "desc":"IC289 Variance Report"},
        "AR_Nameline1_Null_Name_Data":                               {"pattern": BASE+"/abp/ar_reports/AR_Nameline1_Null*.msg",                      "keyword":"",          "desc":"Nameline 1 Null Values in name_data"},
        "AR_Missing_Invoices_AR_GL":                                 {"pattern": BASE+"/abp/ar_reports/AR_Missing_Invoices*.msg",                      "keyword":"",          "desc":"Missing Invoices in AR and GL"},
        "AR_Subscription_Pay_Means_Mismatch_Backend":                {"pattern": BASE+"/abp/ar_reports/AR_Subs_Pay_Means*.msg",                      "keyword":"",          "desc":"Subscription Pay Means Mismatch in Backend"},
        "AR3GWLSTR_Daemon_Stuck":                                    {"pattern": BASE+"/abp/ar_reports/AR3GWLSTR_Stuck*.msg",                        "keyword":"",          "desc":"AR3GWLSTR Daemon Stuck (URGENT)"},
        "AR_DDFeedback_Payment_Errored_Files":                       {"pattern": BASE+"/abp/ar_reports/AR_DD_Payment_Errored*.msg",                    "keyword":"",          "desc":"DDFeedback and AR Payment Errored Files"},
        "AR_WriteOff_Exited_Collection_After_Payment":              {"pattern": BASE+"/abp/ar_reports/AR_WriteOff_Exited_Coll*.msg",                  "keyword":"",          "desc":"Write-Off Account Exited Collection After Payment"},
    },
    "Billing": {
        "BTLSOR_Monitoring_Report":                    {"pattern": BASE+"/btlsor_moni/BTLSOR.log*",                          "keyword":"not_ok",  "desc":"BTLSOR Service Monitor"},
        "Collection_Letters_Monitoring_Report":        {"pattern": BASE+"/collection_letters/collection_letters.log*",       "keyword":"NOT OK",  "desc":"Collection Letters Monitoring"},
        "Reprint_Health_Checkup":                      {"pattern": BASE+"/reprint_health/REPRINT_HEALTH_CHECK.log*",         "keyword":"not_ok",  "desc":"Reprint Health Checkup"},
        "RECEIPT_FAILURE_DETAILS_REPORT_SUBSCRIPTION": {"pattern": BASE+"/subscription_receipt/SUBSCRIPTION_RECEIPT.log*",  "keyword":"not_ok",  "desc":"Subscription Receipt Failure"},
        "RECEIPT_FAILURE_DETAILS_REPORT_UPFORNT":      {"pattern": BASE+"/upfront_receipt/UPFORNT_RECEIPT.log*",             "keyword":"not_ok",  "desc":"Upfront Receipt Failure"},
        "BACKDATE_FAILURE_REPORT":                     {"pattern": BASE+"/Backdate_failed/Backdate.log*",                    "keyword":"NOT_OK",  "desc":"Backdate Failure Report"},
        "PENDING_REBILL_FILES_REPORT":                 {"pattern": BASE+"/pending_rebill/Rebill.log*",                       "keyword":"NOT_OK",  "desc":"Pending Rebill Files"},
        "TC_DUPLICATE_MAP_REPORT":                     {"pattern": BASE+"/duplicate_map/Pendingmap.log*",                    "keyword":"NOT_OK",  "desc":"TC Duplicate Map Report"},
    },
    "TC": {
        "AC1_CONTROL_PROBLEMATIC_FILES_ALERT":            {"pattern": BASE+"/TC/ac1_control_problematic/PROBLEMATIC_CMDB_*.log",     "keyword":"not_ok", "desc":"AC1 Control Problematic Files"},
        "AC1_CONTROL_PROBLEMATIC_EP_FILES_ALERT":         {"pattern": BASE+"/TC/ac1_control_problematic_EP/PROBLEMATIC_EPDB_*.log",  "keyword":"not_ok", "desc":"AC1 Control EP Files Alert"},
        "AC1_MANAGER_THREAD_ALERT":                       {"pattern": BASE+"/TC/ac1_manager_thread_alert/THREAD_CONTROL_*.log",      "keyword":"not_ok", "desc":"AC1 Manager Thread Alert"},
        "ELA_STATS_ALERT":                                {"pattern": BASE+"/TC/ela_statsto/ELA_STATS_*.log",                        "keyword":"not_ok", "desc":"ELA Stats Alert"},
        "IMDG_GRID_NTF_COUNT_ALERT":                      {"pattern": BASE+"/TC/imdg_grid_ntf/region_count_*.log",                   "keyword":"not_ok", "desc":"IMDG Grid Notify Count Alert"},
        "REJECTED_USAGE_ALERT":                           {"pattern": BASE+"/TC/rejected_usage/rejected_event_*.log",                "keyword":"not_ok", "desc":"Rejected Usage Alert"},
        "RTN_DASHBOARD_IMDG_ALERT":                       {"pattern": BASE+"/TC/rtn_dashboard_imdg/RTN_COUNT_*.log",                 "keyword":"not_ok", "desc":"RTN Dashboard IMDG Alert"},
        "UNPROCESSED_COUNT_ALERT_For_EnvFix_ManFix_ReRun":{"pattern": BASE+"/TC/unprocessed_count/ENV_FIX_*.log",                   "keyword":"not_ok", "desc":"Unprocessed Count Alert"},
        "UQ_MONITORING_ALRET":                            {"pattern": BASE+"/TC/uq_monitoring/UQ_CHECK_*.log",                      "keyword":"not_ok", "desc":"UQ Monitoring Alert"},
        "USAGE_BACKLOG_TPS_ONLINE_OFFLINE_ALERT":         {"pattern": BASE+"/TC/usage_backlog_tps/TPS_OFFLINE_*.log",               "keyword":"not_ok", "desc":"Usage Backlog TPS Alert"},
        "File_System_Usage_Report":                       {"pattern": BASE+"/TC/filesystem/OPTUS_File_System*",                 "keyword":"",       "desc":"FS Usage of TC Servers"},
        "AC1_Control_Problematic_Files":                  {"pattern": BASE+"/TC/ac1_control_problematic_files/AC1_CONTROL*.log",    "keyword":"not_ok", "desc":"AC1_CONTROL problematic files (EP & CM)"},
        "TC_Health_Check":                                {"pattern": BASE+"/TC/tc_health_check/Optus_Production_TC_Health*.log",   "keyword":"not_ok", "desc":"Hourly TC Health Check / Sanity"},
        "Rerate_Backlog_Status":                          {"pattern": BASE+"/TC/rerate_backlog/Optus_Production*Rerate*.log",       "keyword":"not_ok", "desc":"TC Rerate Backlog Status"},
        "TC_Bill_Reject_Status":                          {"pattern": BASE+"/TC/reject_status/MAIL_from_REJECT_STATUS*.log",        "keyword":"not_ok", "desc":"BillReject Monitoring for BE"},
        "TC_Process_Crash":                               {"pattern": BASE+"/TC/process_crash/PROCESS_CRASH*.log",                  "keyword":"not_ok", "desc":"Process crash notification (TC servers)"},
        "TC_Usage_Backlog_Alert":                         {"pattern": BASE+"/TC/usage_backlog_alert/*USAGE_BACKLOG*.log",           "keyword":"not_ok", "desc":"TCUSAGE Backlog threshold alert"},
        "TC_Thread_Control_Down":                         {"pattern": BASE+"/TC/thread_control_down/*THREAD*DOWN*.log",             "keyword":"not_ok", "desc":"AC Thread Control Down alert"},
        "AVM1_ES_Alerts":                                 {"pattern": BASE+"/TC/avm1_es_alerts/*AVM1_ES*.log",                      "keyword":"not_ok", "desc":"AVM1 ES stuck-thread alert"},
    },
    "CRM": {
        "CRM_jvm_check":            {"pattern": BASE+"/crm_exception/CRM_logcheck.log.*",     "keyword":"Exception", "desc":"CRM JVM Exception Monitor"},
        "BAP_Report":               {"pattern": BASE+"/bap/CRM_BAP_Report15Mins*",            "keyword":"ALERT!!",   "desc":"BAP Health Report"},
        "BAP_Error_Report":         _METRIC_CHECKS["BAP_Error_Report"],
        "Script_Monitoring_Report": {"pattern": BASE+"/crm_script_moni/script_monitoring_*",  "keyword":"ALERT!!",   "desc":"CRM Script Monitoring Report"},
        "CRM_OBJID_Sequence_Report":{"pattern": BASE+"/crm_OBJID_seq_Report/CRM_ObjidSequence*", "keyword":"ALERT!",  "desc":"CRM OBJID Sequence Report"},
    },
    "Digital": {
        "MCSS_jvm_check": {"pattern": BASE+"/mcss_exception/MCSS_logcheck.log.*", "keyword":"Exception", "desc":"MCSS JVM Exception Monitor"},
        "OMNI_jvm_check": {"pattern": BASE+"/omni_exception/OMNI_logcheck.log.*", "keyword":"Exception", "desc":"Omni Channel JVM Monitor"},
        "BDN_BRN_Report": _METRIC_CHECKS["BDN_BRN_Report"],
    },
    "OMS": {
        "OMS_jvm_check":                                {"pattern": BASE+"/oms_exception/OMS_logcheck.log.*",                        "keyword":"Exception", "desc":"OMS JVM Exception Monitor"},
        "OMS_Collection_Report":                        {"pattern": BASE+"/oms/oms_coll_rep/CollectionReport_NoRequestFromCM_*",      "keyword":"ALERT",     "desc":"OMS Collection Report"},
        "SR_rate_Prepaid_postpaid_completion_response": {"pattern": BASE+"/asom_oms_sr/OMS_OPTUS_OrdersPrepaidandPostpaid_Updated_*", "keyword":"ALERT!!",   "desc":"SR Rate Prepaid/Postpaid Completion"},
    },
    "ASOM": {
        "ASOM_jvm_check":                 {"pattern": BASE+"/asom_exception/ASOM_logcheck.log.*",              "keyword":"Exception", "desc":"ASOM JVM Exception Monitor"},
        "ASOM_Server_Request_Monitoring": {"pattern": BASE+"/asom_reports/ASOM_Server_Req_Count_Monitoring_*",  "keyword":"ALERT!!",   "desc":"ASOM Server Request Monitoring"},
        "ASOM_OBJID_Sequence_Report":     {"pattern": BASE+"/asom_OBJID_seq_Report/ASOM_ObjidSequence*",       "keyword":"ALERT!",    "desc":"ASOM OBJID Sequence Report"},
        "JVM_Threads_Report":             _METRIC_CHECKS["JVM_Threads_Report"],
    },
    "MCO": {
        "MCO_jvm_check": {"pattern": BASE+"/mco_exception/MCO_logcheck.log*", "keyword":"Exception", "desc":"MCO JVM Exception Monitor"},
        "MCO_Server":    {"pattern": BASE+"/mco_url/urllogfile*",             "keyword":"ALERT!!",   "desc":"MCO Server Health Check"},
        "MCO_System_Files_Cleanup": _METRIC_CHECKS["MCO_System_Files_Cleanup"],
    },
    "WSF": {
        "WSF_jvm_check": {"pattern": BASE+"/wsf_exception/WSF_logcheck.log.*", "keyword":"Exception", "desc":"WSF JVM Exception Monitor"},
    },
    "System": {
        "JVM_Health":               {"pattern": BASE+"/healthcheck/JVM_HealthCheck*.log",          "keyword":"DOWN",     "desc":"Weblogic JVM Health Check"},
        "Promotion_Expiry_Forecast":{"pattern": BASE+"/promo_exp_forecast_Report/PromoForecast_*", "keyword":"ALERT!!!", "desc":"Promotion Expiry Forecast"},
        "ANM_Health_Check_Report":  {"pattern": BASE+"/ANM/ANM_healthcheck_report*",               "keyword":"Not_ok",   "desc":"ANM Health Check Report"},
        "File_System_Usage_Report": _METRIC_CHECKS["File_System_Usage_Report"],
    },
}
# Populated by main() at render time (per selected time-machine cutoff).
results = {}
 
@_cache_data_compat(ttl=600, show_spinner=False)
def compute_daily_history(end_day_key, days=30):
    """End-of-day pass / fail / warning counts for the trend chart."""
    end = datetime.strptime(end_day_key, "%Y%m%d").replace(
        hour=23, minute=59, second=59)
    file_index = _build_file_index()
    rows = []
    for i in range(days):
        d = (end - timedelta(days=days - 1 - i)).replace(
            hour=23, minute=59, second=59)
        snap = run_all_checks(d.timestamp(), file_index=file_index)
        passed = failed = warnings = 0
        for prod in snap:
            for r in snap[prod].values():
                st_val = r["status"]
                if st_val == "PASS":
                    passed += 1
                elif st_val == "FAIL":
                    failed += 1
                else:
                    warnings += 1
        rows.append({
            "Date": d.replace(hour=12, minute=0, second=0, microsecond=0),
            "Passed": passed,
            "Failed": failed,
            "Warnings": warnings,
        })
    return pd.DataFrame(rows)
 
def dash_name(check_name):
    return DASHBOARD_NAMES.get(check_name, check_name.replace("_", " ")[:16])
def get_latest_file(pattern):
    files = glob.glob(pattern)
    return max(files, key=file_sort_ts) if files else None
 
def _try_ts(fmt, text):
    try:
        return datetime.strptime(text, fmt).timestamp()
    except ValueError:
        return None
 
def _parse_ts_from_filename(path):
    """Extract a report timestamp embedded in the filename (if any)."""
    base = os.path.basename(path)
    for m in re.finditer(r"(\d{8})_(\d{6})", base):
        ts = _try_ts("%Y%m%d%H%M%S", m.group(1) + m.group(2))
        if ts is not None:
            return ts
    for m in re.finditer(r"(\d{8})(\d{6})", base):
        block, clock = m.group(1), m.group(2)
        if block[:4] in ("2024", "2025", "2026", "2027"):
            ts = _try_ts("%Y%m%d%H%M%S", block + clock)
            if ts is not None:
                return ts
        if block[4:8] in ("2024", "2025", "2026", "2027"):
            ts = _try_ts("%m%d%Y%H%M%S", block + clock)
            if ts is not None:
                return ts
    for m in re.finditer(r"(\d{6})(\d{6})", base):
        d, t = m.group(1), m.group(2)
        if d[4:6] in ("24", "25", "26", "27"):
            ts = _try_ts("%d%m%y%H%M%S", d + t)
            if ts is not None:
                return ts
    for m in re.finditer(r"(\d{4})-(\d{2})-(\d{2})(\d{2})-(\d{2})", base):
        y, mo, da, hh, mi = m.groups()
        ts = _try_ts("%Y-%m-%d %H:%M", "{}-{}-{} {}:{}".format(y, mo, da, hh, mi))
        if ts is not None:
            return ts
    return None
 
def file_report_ts(fp):
    """Logical report time used for time-travel filtering."""
    parsed = _parse_ts_from_filename(fp)
    if parsed is not None:
        return parsed
    try:
        return os.path.getmtime(fp)
    except OSError:
        return 0.0
 
def file_sort_ts(fp):
    """Newest-file ordering for live view (filename schedule vs filesystem mtime)."""
    try:
        mtime = os.path.getmtime(fp)
    except OSError:
        mtime = 0.0
    return max(file_report_ts(fp), mtime)
 
def get_file_asof(pattern, cutoff_ts=None):
    """Newest file matching pattern whose report time is <= cutoff_ts."""
    files = glob.glob(pattern)
    if not files:
        return None
    if cutoff_ts is not None:
        files = [f for f in files if file_report_ts(f) <= cutoff_ts + 1]
        if not files:
            return None
        return max(files, key=file_report_ts)
    return max(files, key=file_sort_ts)
 
@_cache_data_compat(ttl=120, show_spinner=False)
def _build_file_index():
    """Pre-index all report files once (speeds up time-travel + trends)."""
    idx = {}
    for product in CHECKS:
        for name, cfg in CHECKS[product].items():
            idx[(product, name)] = sorted(
                [(f, file_report_ts(f)) for f in glob.glob(cfg["pattern"])],
                key=lambda x: x[1],
            )
    return idx
 
def _pick_from_index(entries, cutoff_ts=None):
    if not entries:
        return None
    if cutoff_ts is None:
        return max((f for f, _ in entries), key=file_sort_ts)
    ts_list = [t for _, t in entries]
    i = bisect.bisect_right(ts_list, cutoff_ts + 1) - 1
    return entries[i][0] if i >= 0 else None
 
def file_age_mins(fp, ref_ts=None):
    if not fp:
        return None
    try:
        ref = ref_ts if ref_ts is not None else time.time()
        return max(0, int((ref - file_report_ts(fp)) / 60))
    except Exception:
        return None
def age_label(mins):
    if mins is None:
        return "no file"
    if mins < 60:
        return "{}m ago".format(mins)
    return "{}h {}m ago".format(mins // 60, mins % 60)
def determine_status(fp, keyword, name=""):
    try:
        content = load_content(fp)
        if name == "JVM_Threads_Report":
            status, _ = jvm_threads_status(content, fp)
            return status, content
        if name == "BDN_BRN_Report":
            status, _ = bdn_brn_status(content, fp)
            return status, content
        if is_tc_report(name):
            status, _ = tc_report_status(name, content, fp)
            return status, content
        if is_abp_email_report(name):
            status, _ = abp_email_report_status(name, content, fp)
            return status, content
        if is_metric_report(name):
            return "PASS", content
        if name in JVM_CHECKS:
            fail_lines = [
                line for line in content.split('\n')
                if 'Exception' in line and '<tr><td>' in line and 'NA' not in line
                and 'No Exception' not in line and 'No Exceptions' not in line
            ]
            return ("FAIL" if fail_lines else "PASS"), content
        return ("FAIL" if keyword in content else "PASS"), content
    except Exception as e:
        return "WARNING", str(e)
# Presence-type alerts are only considered "active" if the alert mail is
# fresher than this many minutes (relative to the viewed time). Older alerts
# are treated as cleared. Keeps time-travel history realistic.
ALERT_FRESH_MINS = 24 * 60
 
def run_all_checks(cutoff_ts=None, file_index=None):
    if file_index is None:
        file_index = _build_file_index()
    results = {}
    for product in CHECKS:
        results[product] = {}
        for name, cfg in CHECKS[product].items():
            latest = _pick_from_index(file_index.get((product, name), []), cutoff_ts)
            age_m  = file_age_mins(latest, cutoff_ts)
            is_alert = name in EMAIL_ALERT_CHECKS
            if not latest:
                if is_alert:
                    status, content = "PASS", "No alert received."
                else:
                    status, content = "WARNING", "Output file not found."
            elif is_alert and age_m is not None and age_m > ALERT_FRESH_MINS:
                # An old alert mail — treat as cleared.
                status, content = "PASS", "No active alert (last alert cleared)."
                latest = None
            else:
                status, content = determine_status(latest, cfg["keyword"], name)
            results[product][name] = {
                "status":  status,
                "file":    latest,
                "content": content,
                "keyword": cfg["keyword"],
                "desc":    cfg["desc"],
                "age_lbl": age_label(age_m if latest else None),
            }
            if not latest and is_alert:
                results[product][name]["info_badge"] = "OK"
            if latest:
                enrich_result(name, content, latest, results[product][name])
    return results
def parse_sysbounce(content):
    daemons = []
    seen = set()
    clean = re.sub(r'<(script|style)[^>]*>.*?</(script|style)>', '', content,
                   flags=re.IGNORECASE | re.DOTALL)
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', clean, re.IGNORECASE | re.DOTALL)
    for row in rows:
        cells_raw = re.findall(r'<td[^>]*>(.*?)</td>', row, re.IGNORECASE | re.DOTALL)
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells_raw]
        cells = [c for c in cells if c]
        if len(cells) < 2:
            continue
        for cell in cells:
            upper = cell.strip().upper()
            if upper in ("UP", "DOWN"):
                daemon_name = cells[0] if cells[0].strip().upper() not in ("UP", "DOWN", "STATUS", "STATE") else (cells[1] if len(cells) > 1 else "")
                daemon_name = daemon_name.strip()
                if daemon_name and daemon_name not in seen:
                    seen.add(daemon_name)
                    daemons.append((daemon_name, upper))
                break
    if not daemons:
        plain = re.sub(r'<[^>]+>', ' ', clean)
        matches = re.findall(r'([A-Za-z0-9_\-\.]+)\s*[:\-]\s*(UP|DOWN)', plain, re.IGNORECASE)
        for daemon_name, status in matches:
            if daemon_name not in seen and daemon_name.upper() not in ("STATUS", "STATE"):
                seen.add(daemon_name)
                daemons.append((daemon_name, status.upper()))
    return daemons
def _extract_td_cells(row):
    cells_raw = re.findall(r'<td[^>]*>(.*?)</td>', row, re.IGNORECASE | re.DOTALL)
    if cells_raw:
        return [re.sub(r'<[^>]+>', '', c).strip() for c in cells_raw]
    parts = re.split(r'<td[^>]*>', row, flags=re.IGNORECASE)
    cells = []
    for part in parts[1:]:
        text = re.split(r'<(?:td|th|/tr)[^>]*>', part, flags=re.IGNORECASE)[0]
        text = re.sub(r'<[^>]+>', '', text).strip()
        if text:
            cells.append(text)
    return cells
def _is_all_ok_message(text):
    t = text.lower()
    return "no exception" in t or "no exceptions" in t or ("all jvm" in t and "started properly" in t)
def parse_jvm_logcheck(content):
    jvms = []
    seen = set()
    h2_match = re.search(r'<h2[^>]*>(.*?)</h2>', content, re.IGNORECASE | re.DOTALL)
    section_title = re.sub(r'<[^>]+>', '', h2_match.group(1)).strip() if h2_match else "All JVMs"
    clean = re.sub(r'<(script|style)[^>]*>.*?</(script|style)>', '', content,
                   flags=re.IGNORECASE | re.DOTALL)
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', clean, re.IGNORECASE | re.DOTALL)
    for row in rows:
        if '<h2' in row.lower():
            continue
        cells = _extract_td_cells(row)
        if len(cells) < 2:
            continue
        if cells[0].upper() in ("JVM", "SERVER", "INSTANCE", "NAME"):
            continue
        jvm_name = cells[0]
        detail   = cells[1]
        if jvm_name.upper() in ("NA", "N/A", "-", "NONE", ""):
            jvm_name = section_title
        if _is_all_ok_message(detail):
            status = "OK"
        elif detail.upper() in ("NA", "N/A", "NONE", "-", ""):
            status = "OK"
            detail = "No exceptions"
        elif "exception" in detail.lower() and not _is_all_ok_message(detail):
            status = "FAIL"
        else:
            status = "OK"
        key = (jvm_name, detail, status)
        if jvm_name and key not in seen:
            seen.add(key)
            jvms.append({"name": jvm_name, "detail": detail, "status": status})
    return jvms
def _billing_display_name(check_name):
    return BILLING_DISPLAY_NAMES.get(check_name, check_name)
def is_btlsor_style_log(content):
    return bool(re.search(
        r'\d{2}/\w{3}/\d{4}\s+\d{2}:\d{2}:\d{2}\s+\d+\s+\d+\s+OK',
        content or "", re.IGNORECASE
    ))
def parse_btlsor_style_log(content, check_name="BTLSOR"):
    items = []
    pattern = re.compile(
        r'^(\d{2}/\w{3}/\d{4}\s+\d{2}:\d{2}:\d{2})\s+(\d+)\s+(\d+)\s+(OK|FAIL|not_ok|NOT\s*OK)\s*$',
        re.IGNORECASE
    )
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        m = pattern.match(line)
        if m:
            ts, batch_id, count, status_text = m.groups()
            st_val = "OK" if status_text.upper().strip() == "OK" else "FAIL"
            items.append({"name": ts, "detail": "Batch: {} | Count: {}".format(batch_id, count), "status": st_val})
        else:
            parts = line.split()
            if len(parts) >= 4 and parts[-1].upper() in ("OK", "FAIL", "NOT_OK"):
                ts = "{} {}".format(parts[0], parts[1]) if "/" in parts[0] else parts[0]
                st_val = "OK" if parts[-1].upper() == "OK" else "FAIL"
                items.append({"name": ts, "detail": " | ".join(parts[2:-1]), "status": st_val})
    if not items and content.strip():
        items.append({
            "name": _billing_display_name(check_name),
            "detail": content.strip()[:200],
            "status": "OK" if "not_ok" not in content.lower() else "FAIL",
        })
    return items
def _status_from_last_field(text, keyword=""):
    text = text.strip()
    if not text:
        return "WARN"
    if "," in text:
        last_field = text.split(",")[-1].strip().upper()
        if last_field in ("OK", "PASS", "GOOD", "SUCCESS"):
            return "OK"
        if last_field in ("FAIL", "NOT_OK", "NOT OK", "ERROR", "FAILED"):
            return "FAIL"
    upper = text.upper()
    if upper in ("OK", "PASS", "GOOD"):
        return "OK"
    if keyword and keyword.lower() in text.lower() and upper not in ("OK", "PASS"):
        return "FAIL"
    return "OK"
def parse_billing_log(content, keyword, check_name, overall_status="PASS"):
    content = (content or "").strip()
    display = _billing_display_name(check_name)
    if is_btlsor_style_log(content):
        return parse_btlsor_style_log(content, check_name)
    if not content:
        if overall_status == "PASS":
            return [{"name": display, "detail": "No issues reported", "status": "OK"}]
        return [{"name": display, "detail": "Log empty", "status": "WARN"}]
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    if len(lines) == 1 and len(lines[0]) < 200:
        return [{"name": display, "detail": lines[0], "status": _status_from_last_field(lines[0], keyword)}]
    items = []
    for i, line in enumerate(lines):
        items.append({
            "name": "Run {}".format(i + 1) if len(lines) > 3 else display,
            "detail": line[:250],
            "status": _status_from_last_field(line, keyword),
        })
    return items if items else [{
        "name": display, "detail": content[:300],
        "status": "FAIL" if (keyword and keyword in content) else "OK",
    }]
def parse_generic_log(content, keyword, check_name, overall_status="PASS"):
    content = (content or "").strip()
    if not content:
        return [{"name": check_name, "detail": "No issues reported", "status": "OK" if overall_status == "PASS" else "WARN"}]
    if is_btlsor_style_log(content):
        return parse_btlsor_style_log(content, check_name)
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    if len(lines) == 1 and len(lines[0]) < 200 and '<' not in lines[0]:
        return [{"name": check_name, "detail": lines[0], "status": _status_from_last_field(lines[0], keyword)}]
    items = []
    for i, line in enumerate(lines):
        items.append({
            "name": "Line {}".format(i + 1) if len(lines) > 3 else check_name,
            "detail": line[:250],
            "status": _status_from_last_field(line, keyword),
        })
    return items if items else [{
        "name": check_name, "detail": content[:300],
        "status": "FAIL" if (keyword and keyword in content) else "OK",
    }]
def _escape_html(text):
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
 
def _grid_item_html(item, ok_label, fail_label, accent_border=""):
    status = item["status"]
    if status == "FAIL":
        dot_c, bg_c, bd_c, badge_c, label = "#DC2626", "#FFF5F5", "#FECACA", "d-down", fail_label
    elif status == "WARN":
        dot_c, bg_c, bd_c, badge_c, label = "#D97706", "#FFFBEB", "#FDE68A", "d-warn", "WARN"
    else:
        dot_c, bg_c, bd_c, badge_c, label = "#16A34A", "#F0FDF4", "#BBF7D0", "d-up", ok_label
    html = (
        '<div class="pd-grid-item">'
        '<div class="daemon-row" style="background:{};border-color:{};{}">'
        '<span class="daemon-dot" style="background:{}"></span>'
        '<span class="daemon-nm">{}</span>'
        '<span class="daemon-st {}">{}</span>'
        '</div>'
    ).format(bg_c, bd_c, accent_border, dot_c, _escape_html(item["name"]), badge_c, label)
    detail = item.get("detail", "")
    if detail and detail.upper() not in (item["name"].upper(), label, "OK"):
        html += '<div class="jvm-exc">{}</div>'.format(_escape_html(detail))
    html += '</div>'
    return html
def render_status_grid_html(items, title="Report Status", ok_label="OK", fail_label="FAIL", accent_color=None):
    ok_count   = sum(1 for i in items if i["status"] == "OK")
    fail_count = sum(1 for i in items if i["status"] == "FAIL")
    warn_count = sum(1 for i in items if i["status"] == "WARN")
    border = "border-left:3px solid {}!important;".format(accent_color) if accent_color else ""
    html = '<div class="pd-inline-log">'
    html += '<div class="pd-inline-log-title">{}</div>'.format(_escape_html(title))
    html += (
        '<div class="pd-inline-log-summary">'
        '<span>Total: {}</span>'
        '<span class="pd-sum-ok">{}: {}</span>'
        '<span class="pd-sum-fail">{}: {}</span>'
    ).format(len(items), ok_label, ok_count, fail_label, fail_count)
    if warn_count:
        html += '<span class="pd-sum-warn">WARN: {}</span>'.format(warn_count)
    html += '</div><div class="pd-grid-2col">'
    for item in items:
        html += _grid_item_html(item, ok_label, fail_label, border)
    html += '</div></div>'
    return html
def get_log_panel_html(product, name, r):
    if r["status"] == "WARNING":
        return (
            '<div class="pd-log-panel">'
            '<div class="pd-log-warn">File not found: {}</div>'
            '</div>'
        ).format(_escape_html(CHECKS[product][name]["pattern"]))
    if not r["file"]:
        return ""
    content = r["content"]
    keyword = r["keyword"]
    overall_status = r["status"]
    if name in BILLING_CHECKS:
        items = parse_billing_log(content, keyword, name, overall_status)
        title = "Billing Run History" if is_btlsor_style_log(content or "") else "Billing Report Status"
        return '<div class="pd-log-panel">' + render_status_grid_html(
            items, title=title, accent_color="#7C3AED") + '</div>'
    if name in JVM_CHECKS:
        jvms = parse_jvm_logcheck(content)
        if jvms:
            return '<div class="pd-log-panel">' + render_status_grid_html(
                jvms, title="JVM Status") + '</div>'
        return '<div class="pd-log-panel"><pre class="pd-raw-log">{}</pre></div>'.format(
            _escape_html(content[:5000]))
    if name in HTML_REPORT_CHECKS:
        daemons = parse_sysbounce(content)
        if daemons:
            items = [{"name": n, "detail": s, "status": "OK" if s == "UP" else "FAIL"} for n, s in daemons]
            return '<div class="pd-log-panel">' + render_status_grid_html(
                items, title="Daemon Status", ok_label="UP", fail_label="DOWN") + '</div>'
        return '<div class="pd-log-panel"><pre class="pd-raw-log">{}</pre></div>'.format(
            _escape_html(content[:5000]))
    metric_panel = get_metric_panel_html(name, r, content)
    if metric_panel:
        return metric_panel
    items = parse_generic_log(content, keyword, name, overall_status)
    return '<div class="pd-log-panel">' + render_status_grid_html(
        items, title="Report Status") + '</div>'
def product_summary(prod):
    prod_results = results[prod]
    n_pass  = sum(1 for r in prod_results.values() if r["status"] == "PASS")
    n_fail  = sum(1 for r in prod_results.values() if r["status"] == "FAIL")
    n_warn  = sum(1 for r in prod_results.values() if r["status"] == "WARNING")
    n_total = len(prod_results)
    if n_fail > 0:
        accent_cls, badge, badge_cls = "accent-fail", "{} FAIL".format(n_fail), "st-fail"
    elif n_warn > 0:
        accent_cls, badge, badge_cls = "accent-warn", "{} WARN".format(n_warn), "st-warn"
    else:
        accent_cls, badge, badge_cls = "accent-ok", "ALL OK", "st-ok"
    return n_pass, n_total, accent_cls, badge, badge_cls
def render_home_unified_card(prod):
    n_pass, n_total, accent_cls, badge, badge_cls = product_summary(prod)
    order = {"FAIL": 0, "WARNING": 1, "PASS": 2}
    items = list(results[prod].items())
    items.sort(key=lambda kv: order.get(kv[1]["status"], 3))
    chips_html = ""
    for name, r in items:
        label = dash_name(name)
        status = r["status"]
        if status == "FAIL":
            chip_cls, bdg = "cchip-fail", "FAIL"
        elif status == "WARNING":
            chip_cls, bdg = "cchip-warn", "WARN"
        else:
            m_cls, m_bdg = row_badge(name, r, status)
            if m_cls == "row-info":
                chip_cls, bdg = "cchip-info", (m_bdg or "OK")
            else:
                chip_cls, bdg = "cchip-pass", "OK"
        tip = "{} — {}: {}".format(prod, label, bdg)
        q = (label + " " + prod + " " + name).lower()
        chips_html += (
            '<span class="cchip {cls}" title="{tip}" data-q="{q}">{lbl}</span>'
        ).format(cls=chip_cls, tip=_escape_html(tip), q=_escape_html(q), lbl=_escape_html(label))
    return (
        '<div class="prod-unified-card {accent_cls}" data-product="{slug}">'
        '<a class="prod-link" href="#m-{slug}">'
        '<div class="prod-card-top">'
        '<div class="prod-summary-top">'
        '<span class="prod-summary-nm">{prod}</span>'
        '<span class="prod-grid-st {badge_cls}">{badge}</span>'
        '</div>'
        '<div class="prod-summary-sub">{n_pass}/{n_total} passed</div>'
        '</div></a>'
        '<div class="prod-card-chips">{chips}</div>'
        '</div>'
    ).format(
        accent_cls=accent_cls, slug=prod, prod=prod,
        badge_cls=badge_cls, badge=badge,
        n_pass=n_pass, n_total=n_total, chips=chips_html)
 
def status_reason(product, name, r):
    """Produce a concise, human-readable explanation of the check outcome."""
    status = r["status"]
    if status == "WARNING":
        return "Output file could not be found — the check may not have run."
    content = r.get("content") or ""
    keyword = r.get("keyword") or ""
    if status == "PASS":
        if is_metric_report(name) or is_jvm_threads_report(name):
            badge = info_badge(name, r)
            if name == "BAP_Error_Report":
                return "{} pending record(s) across BAP services.".format(badge)
            if name == "MCO_System_Files_Cleanup":
                return "Latest cleanup execution: {} processed.".format(badge)
            if name == "File_System_Usage_Report":
                return "Highest file system usage: {}.".format(badge)
            if name == "JVM_Threads_Report":
                return "JVM threads report: {} across {} servers.".format(
                    badge, r.get("jvm_threads_data", {}).get("server_count", 0))
            if name == "BDN_BRN_Report":
                d = r.get("bdn_brn_data", {})
                return "E-Bill files all clear — {} files complete, no stuck/damaged files.".format(
                    d.get("total_files", 0))
            if is_tc_report(name):
                if name in TC_ALERT_CHECKS:
                    return "No alert received — component healthy."
                if name == "Rerate_Backlog_Status":
                    return "Rerate backlog: {} customers pending.".format(badge)
                if name == "TC_Bill_Reject_Status":
                    return "{} bill reject record(s) reported.".format(badge)
                return "No issues detected in the latest report."
            if is_abp_email_report(name):
                return "No alert received — component healthy."
        return "No issues detected in the latest report."
    if name == "JVM_Threads_Report":
        data = r.get("jvm_threads_data") or parse_jvm_threads_report(content, r.get("file") or "")
        reds = [s for sec in data.get("sections", []) for cl in sec.get("clusters", [])
                for s in cl.get("servers", []) if s.get("is_red")]
        if reds:
            names = ", ".join(s["server_name"] for s in reds[:4])
            more = "" if len(reds) <= 4 else " and {} more".format(len(reds) - 4)
            return "{} RED server(s): {}{}.".format(len(reds), names, more)
        return "RED items detected in JVM threads report."
    if is_tc_report(name):
        data = r.get("tc_data") or {}
        if name in TC_ALERT_CHECKS:
            return "Alert email received: {}".format(data.get("headline") or r.get("desc") or "TC alert")
        bad = data.get("bad_titles") or set()
        if bad:
            return "Issue detected in: {}.".format(", ".join(t for t in bad if t))
        return "The report matched a failure condition."
    if is_abp_email_report(name):
        data = r.get("abp_email_data") or {}
        return "Alert email received: {}".format(
            data.get("headline") or data.get("subject") or r.get("desc") or "ABP alert")
    if name == "BDN_BRN_Report":
        data = r.get("bdn_brn_data") or parse_bdn_brn_report(content, r.get("file") or "")
        parts = []
        stuck = data.get("stuck_files", [])
        damaged = data.get("files", {}).get("damaged", 0)
        rejected = next((c for lbl, c in data.get("statuses", []) if lbl == "REJECTED"), 0)
        if stuck:
            parts.append("{} stuck file(s) moved for reprocess".format(len(stuck)))
        if damaged:
            parts.append("{} damaged file(s)".format(damaged))
        if rejected:
            parts.append("{} rejected file(s)".format(rejected))
        if parts:
            return "E-Bill file issue detected: {}.".format("; ".join(parts))
        return "Stuck or missing E-Bill files detected."
    # FAIL — try to explain what triggered it.
    if name in JVM_CHECKS:
        jvms = parse_jvm_logcheck(content)
        fails = [j for j in jvms if j["status"] == "FAIL"]
        if fails:
            names = ", ".join(j["name"] for j in fails[:3])
            more = "" if len(fails) <= 3 else " and {} more".format(len(fails) - 3)
            return "{} JVM exception(s) detected: {}{}.".format(len(fails), names, more)
        return "Exception activity detected in the JVM log."
    if name in HTML_REPORT_CHECKS:
        daemons = parse_sysbounce(content)
        downs = [n for n, s in daemons if s.upper() != "UP"]
        if downs:
            names = ", ".join(downs[:4])
            more = "" if len(downs) <= 4 else " and {} more".format(len(downs) - 4)
            return "{} service(s) reported DOWN: {}{}.".format(len(downs), names, more)
        return "One or more services reported DOWN."
    if keyword:
        cnt = content.count(keyword)
        if cnt:
            return "Alert marker '{}' found {} time(s) in the report.".format(keyword, cnt)
    return "The report matched an alert/failure condition."
 
def render_detail_row_html(product, name, r):
    status = r["status"]
    if status == "FAIL":
        row_cls, badge_txt, det_cls, reason_cls = "row-fail", "FAIL", "det-fail", "reason-fail"
    elif status == "WARNING":
        row_cls, badge_txt, det_cls, reason_cls = "row-warn", "WARN", "det-warn", "reason-warn"
    else:
        m_cls, m_bdg = row_badge(name, r, status)
        if m_cls:
            row_cls, badge_txt, det_cls, reason_cls = m_cls, m_bdg, "det-info", "reason-pass"
        else:
            row_cls, badge_txt, det_cls, reason_cls = "row-pass", "OK", "det-pass", "reason-pass"
    label = "{} — {}".format(product, dash_name(name))
    desc = r.get("desc") or ""
    reason = status_reason(product, name, r)
    panel = get_log_panel_html(product, name, r)
    sub = '<div class="pd-row-sub">'
    if desc:
        sub += '<span class="pd-row-desc">{}</span>'.format(_escape_html(desc))
    sub += '<span class="pd-row-reason {}">{}</span>'.format(reason_cls, _escape_html(reason))
    sub += '</div>'
    return (
        '<details class="pd-row-det {det_cls}">'
        '<summary>'
        '<div class="pd-row {row_cls}">'
        '<span class="pd-row-name">{label}</span>'
        '<span class="pd-row-age">{age}</span>'
        '<span class="pd-row-badge">{badge}</span>'
        '<span class="pd-log-toggle">&#9656;</span>'
        '</div>'
        '{sub}'
        '</summary>'
        '{panel}'
        '</details>'
    ).format(
        det_cls=det_cls, row_cls=row_cls,
        label=_escape_html(label), age=r["age_lbl"], badge=badge_txt,
        sub=sub, panel=panel
    )
 
def render_product_modal(product):
    n_pass, n_total, accent_cls, badge, badge_cls = product_summary(product)
    body = ""
    for group_name, items in group_results(product):
        body += '<div class="pd-group-hdr">{}</div>'.format(_escape_html(group_name))
        for name, r in items:
            body += render_detail_row_html(product, name, r)
    return (
        '<div id="m-{slug}" class="modal-overlay">'
        '<div class="modal-box">'
        '<div class="modal-hdr">'
        '<div><span class="modal-title">{prod}</span>'
        '<span class="modal-sub">{n_pass}/{n_total} passed &middot; {badge}</span></div>'
        '<a class="modal-close" href="#">&times;</a>'
        '</div>'
        '<div class="modal-body">{body}</div>'
        '</div></div>'
    ).format(
        slug=product, prod=product, n_pass=n_pass, n_total=n_total,
        badge=badge, body=body
    )
 
def render_issues_modal():
    order = {"FAIL": 0, "WARNING": 1, "PASS": 2}
    total_iss = 0
    body = ""
    for p in results:
        iss = [(n, r) for n, r in results[p].items() if r["status"] != "PASS"]
        if not iss:
            continue
        total_iss += len(iss)
        body += '<div class="pd-group-hdr">{}</div>'.format(_escape_html(p))
        for name, r in sorted(iss, key=lambda x: order.get(x[1]["status"], 3)):
            body += render_detail_row_html(p, name, r)
    if not total_iss:
        body = ('<div style="padding:22px 18px;color:#15803D;font-weight:700;font-size:14px;">'
                '&#10003; No Active Issues.</div>')
    return (
        '<div id="m-Issues" class="modal-overlay">'
        '<div class="modal-box">'
        '<div class="modal-hdr">'
        '<div><span class="modal-title">Active Issues</span>'
        '<span class="modal-sub">{n} check(s) need attention</span></div>'
        '<a class="modal-close" href="#">&times;</a>'
        '</div>'
        '<div class="modal-body">{body}</div>'
        '</div></div>'
    ).format(n=total_iss, body=body)
# ── Time machine ───────────────────────────────────────────────────────────────
HISTORY_DAYS = 30
 
def _init_time_session(now):
    if "time_machine_live" not in st.session_state:
        st.session_state.time_machine_live = True
    if "time_machine_pick" not in st.session_state:
        st.session_state.time_machine_pick = now
    if st.session_state.time_machine_live:
        st.session_state.time_machine_pick = now
 
def render_header_html(title, ts_now, is_live=True):
    mode_cls = "is-live" if is_live else "is-hist"
    mode_txt = "Live" if is_live else "Historical"
    return (
        '<div class="page-hdr">'
        '<div class="hdr-spacer"></div>'
        '<div class="hdr-center">'
        '<div class="page-title">{title}</div>'
        '<div class="page-subtitle">Operational health monitoring</div>'
        '</div>'
        '<div class="page-meta">'
        '<span class="mode-pill {mode_cls}">{mode_txt}</span>'
        '<span class="page-ts">{ts}</span>'
        '</div>'
        '</div>'
    ).format(title=title, ts=ts_now, mode_cls=mode_cls, mode_txt=mode_txt)
 
def render_unified_header(now):
    """Merged header: title row + live toggle, datetime, auto-refresh."""
    _init_time_session(now)
    start = (now - timedelta(days=HISTORY_DAYS)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    if "auto_refresh" not in st.session_state:
        st.session_state.auto_refresh = True
    live_on = st.session_state.time_machine_live
    sel = st.session_state.time_machine_pick
    ts_display = (now if live_on else sel).strftime("%Y-%m-%d  %H:%M:%S")
    st.markdown(
        render_header_html("Production Validation Dashboard", ts_display, live_on),
        unsafe_allow_html=True)
    # Single row only — old Streamlit forbids columns inside columns.
    c_dt, c_ar, c_live, c_trend = st.columns([2.4, 1, 0.5, 0.5])
    with c_dt:
        sel = _datetime_input_compat(
            "View state as of",
            min_value=start,
            max_value=now,
            step=timedelta(minutes=15),
            key="time_machine_pick",
            disabled=live_on,
        )
    with c_ar:
        st.checkbox(
            "Auto-refresh (5m)",
            key="auto_refresh",
            disabled=not live_on,
            help="Reload the dashboard every 5 minutes in Live mode",
        )
    with c_live:
        live_on = _toggle_compat("Live", key="time_machine_live")
    with c_trend:
        _toggle_compat(
            "Trend",
            key="show_trend",
            help="Show 30-day status trend chart below the scoreboard",
        )
    if live_on:
        return None, now, True
    if sel < start:
        sel = start
    if sel > now:
        sel = now
    return sel.timestamp(), sel, False
 
def render_auto_refresh(is_live):
    if is_live and st.session_state.get("auto_refresh", True):
        components.html(
            "<script>setTimeout(function(){window.parent.location.reload();},300000);</script>",
            height=0,
        )
def render_trend_chart(end_day_key):
    df = compute_daily_history(end_day_key, days=HISTORY_DAYS)
    if df.empty:
        return
    melted = df.melt(
        id_vars=["Date"],
        value_vars=["Passed", "Failed", "Warnings"],
        var_name="Status",
        value_name="Count",
    )
    color_scale = alt.Scale(
        domain=["Passed", "Failed", "Warnings"],
        range=["#16A34A", "#DC2626", "#D97706"],
    )
    chart = (
        alt.Chart(melted)
        .mark_line(point={"size": 40}, strokeWidth=2)
        .encode(
            x=alt.X(
                "Date:T",
                title=None,
                axis=alt.Axis(format="%b %d", labelAngle=0, tickCount=8),
            ),
            y=alt.Y("Count:Q", title="Checks", scale=alt.Scale(zero=True)),
            color=alt.Color(
                "Status:N",
                scale=color_scale,
                legend=alt.Legend(
                    orient="top", direction="horizontal", title=None,
                ),
            ),
            tooltip=[
                alt.Tooltip("Date:T", format="%Y-%m-%d"),
                "Status:N",
                "Count:Q",
            ],
        )
        .properties(height=220)
        .configure_axis(
            labelFontSize=11, titleFontSize=11, gridColor="#E2E8F0",
        )
        .configure_view(strokeWidth=0)
    )
    with _bordered_container_compat():
        st.markdown(
            '<div class="trend-title">Status trend · last {} days</div>'.format(
                HISTORY_DAYS),
            unsafe_allow_html=True,
        )
        _altair_chart_compat(chart)
 
def render_trend_section(end_day_key):
    if not st.session_state.get("show_trend", False):
        return
    with st.spinner("Loading 30-day trend…"):
        render_trend_chart(end_day_key)
 
def render_scoreboard(total, passed, failed, warnings, health_pct, h_color, dot_color):
    st.markdown(
        '<div class="sb-outer"><div class="sb-grid">'
        '<div class="sb-cell sb-total sb-click" id="sb-all" title="Show all checks">'
        '<div class="sb-clbl">Total checks</div><div class="sb-cval">{}</div></div>'
        '<div class="sb-cell sb-pass sb-click" id="sb-pass" title="Filter passed">'
        '<div class="sb-clbl">Passed</div><div class="sb-cval">{}</div></div>'
        '<div class="sb-cell sb-fail sb-click" id="sb-fail" title="Filter failing">'
        '<div class="sb-clbl">Failed</div><div class="sb-cval">{}</div></div>'
        '<div class="sb-cell sb-warn sb-click" id="sb-warn" title="Filter warnings">'
        '<div class="sb-clbl">Warnings</div><div class="sb-cval">{}</div></div>'
        '<div class="sb-last"><div class="sb-clbl">Health score</div>'
        '<div class="sb-cval" style="color:{}">{}</div>'
        '<div class="sb-bar"><div class="sb-barfg" style="width:{}%;background:{}"></div></div>'
        '<div class="sb-barlb">{} of {} healthy</div></div>'
        '</div></div>'.format(
            total, passed, failed, warnings,
            h_color, "{}%".format(health_pct), health_pct, h_color, passed, total),
        unsafe_allow_html=True)
 
def _filter_counts():
    n_fail = n_warn = n_info = n_green = 0
    for p in results:
        for name, r in results[p].items():
            status = r["status"]
            if status == "FAIL":
                n_fail += 1
            elif status == "WARNING":
                n_warn += 1
            else:
                m_cls, _ = row_badge(name, r, status)
                if m_cls == "row-info":
                    n_info += 1
                else:
                    n_green += 1
    return n_fail, n_warn, n_info, n_green
 
def render_filter_bar():
    n_fail, n_warn, n_info, n_green = _filter_counts()
    n_total = n_fail + n_warn + n_info + n_green
    def btn(fid, label, count):
        return (
            '<div class="flt-btn" id="{fid}">{label}'
            '<span class="flt-n">{count}</span></div>'
        ).format(fid=fid, label=label, count=count)
    return (
        '<div class="filter-strip"><div class="flt-bar">'
        '<span class="flt-lead">Show</span>'
        + btn("fb-all", "All", n_total)
        + btn("fb-fail", "Failing", n_fail)
        + btn("fb-warn", "Warnings", n_warn)
        + btn("fb-info", "Info", n_info)
        + btn("fb-pass", "Passed", n_green)
        + '<input type="text" id="chk-search" class="search-inp" '
        'placeholder="Search checks…" autocomplete="off" />'
        + '</div></div>'
    )
 
FILTER_JS = """
<script>
(function () {
    var doc = window.parent.document;
    var ls  = window.parent.localStorage;
    var map = {"fb-all":"all","fb-fail":"fail","fb-warn":"warn","fb-info":"info","fb-pass":"pass",
               "sb-all":"all","sb-fail":"fail","sb-warn":"warn","sb-pass":"pass"};
    function chipVisible(chip, flt) {
        if (chip.classList.contains("search-hide")) return false;
        if (flt === "all") return true;
        if (flt === "fail") return chip.classList.contains("cchip-fail");
        if (flt === "warn") return chip.classList.contains("cchip-warn");
        if (flt === "info") return chip.classList.contains("cchip-info");
        if (flt === "pass") return chip.classList.contains("cchip-pass");
        return true;
    }
    function refreshCards() {
        var wrap = doc.querySelector(".home-wrap");
        if (!wrap) return;
        var flt = wrap.getAttribute("data-flt") || "all";
        doc.querySelectorAll(".prod-unified-card").forEach(function (card) {
            var chips = card.querySelectorAll(".cchip");
            var any = false;
            chips.forEach(function (c) { if (chipVisible(c, flt)) any = true; });
            card.classList.toggle("search-hide-card", !any);
        });
        var grid = doc.querySelector(".home-grid");
        var empty = doc.querySelector(".home-empty");
        if (grid && empty) {
            var shown = grid.querySelectorAll(".prod-unified-card:not(.search-hide-card)");
            var fltHide = flt !== "all" && shown.length === 0;
            var allHide = doc.querySelectorAll(".prod-unified-card:not(.search-hide-card)").length === 0;
            empty.style.display = allHide ? "block" : "none";
        }
    }
    function applySearch(q) {
        q = (q || "").toLowerCase().trim();
        doc.querySelectorAll(".cchip").forEach(function (el) {
            var txt = (el.getAttribute("data-q") || el.textContent || "").toLowerCase();
            el.classList.toggle("search-hide", q && txt.indexOf(q) === -1);
        });
        refreshCards();
    }
    function apply(f) {
        var wrap = doc.querySelector(".home-wrap");
        if (!wrap) return;
        wrap.setAttribute("data-flt", f);
        for (var id in map) {
            var el = doc.getElementById(id);
            if (el) { el.classList.toggle("flt-on", map[id] === f); }
        }
        try { ls.setItem("stflt", f); } catch (e) {}
        refreshCards();
    }
    function wire() {
        var wrap = doc.querySelector(".home-wrap");
        if (!wrap) { return false; }
        for (var id in map) {
            (function (id) {
                var el = doc.getElementById(id);
                if (el && !el.dataset.bound) {
                    el.dataset.bound = "1";
                    el.addEventListener("click", function () { apply(map[id]); });
                }
            })(id);
        }
        var search = doc.getElementById("chk-search");
        if (search && !search.dataset.bound) {
            search.dataset.bound = "1";
            search.addEventListener("input", function () { applySearch(search.value); });
            try {
                var savedQ = ls.getItem("stsearch") || "";
                if (savedQ) { search.value = savedQ; applySearch(savedQ); }
            } catch (e) {}
            search.addEventListener("input", function () {
                try { ls.setItem("stsearch", search.value); } catch (e) {}
            });
        }
        var saved = "all";
        try { saved = ls.getItem("stflt") || "all"; } catch (e) {}
        apply(saved);
        return true;
    }
    var tries = 0;
    var timer = setInterval(function () {
        tries += 1;
        if (wire() || tries > 40) { clearInterval(timer); }
    }, 50);
})();
</script>
"""
 
 
def render_home_page():
    products = list(CHECKS.keys())
    cards = "".join(render_home_unified_card(p) for p in products)
    grid = (
        '<div class="home-grid">'
        + cards
        + '<div class="home-empty">No checks match this filter.</div>'
        + '</div>'
    )
    issue_count = sum(1 for p in results for r in results[p].values() if r["status"] != "PASS")
    issues_link = ""
    if issue_count:
        issues_link = (
            '<div class="issues-link">'
            '<a href="#m-Issues">View {} Active Issue(s) &#8599;</a></div>'.format(issue_count))
    st.markdown(
        '<div class="home-wrap" data-flt="all">' + render_filter_bar() + grid + '</div>' + issues_link,
        unsafe_allow_html=True)
    modals = "".join(render_product_modal(p) for p in products)
    modals += render_issues_modal()
    st.markdown(modals, unsafe_allow_html=True)
    components.html(FILTER_JS, height=0)
 
def main():
    global results
    now = datetime.now()
    cutoff, sel, is_live = render_unified_header(now)
    results = run_all_checks(cutoff)
    total = passed = failed = warnings = 0
    for prod in results:
        for r in results[prod].values():
            total += 1
            if r["status"] == "PASS":
                passed += 1
            elif r["status"] == "FAIL":
                failed += 1
            else:
                warnings += 1
    health_pct = int(round(100.0 * passed / total)) if total else 0
    h_color    = "#16A34A" if health_pct == 100 else "#D97706" if health_pct > 90 else "#DC2626"
    dot_color  = "#DC2626" if failed > 0 else "#D97706" if warnings > 0 else "#16A34A"
    end_day = (now if is_live else sel).strftime("%Y%m%d")
    render_scoreboard(total, passed, failed, warnings, health_pct, h_color, dot_color)
    render_trend_section(end_day)
    render_home_page()
    render_auto_refresh(is_live)
 
if __name__ == "__main__":
    main()
 
 
