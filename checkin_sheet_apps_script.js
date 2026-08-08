/**
 * Standalone Apps Script + Google Sheet backup for check-ins.
 *
 * Sheet: https://docs.google.com/spreadsheets/d/15znpZwKP4wYSQlkyMg70m5swdVWPn7i36oeB6B1xdp0/
 * Deploy → Web app → Anyone → put URL in Render CHECKIN_SHEET_WEBHOOK
 * Render CHECKIN_SHEET_SECRET must match SECRET below.
 */
const SHEET_ID = "15znpZwKP4wYSQlkyMg70m5swdVWPn7i36oeB6B1xdp0";
const SECRET = "ngpa2026-checkin";
const SHEET_NAME = "Checkins";

function doPost(e) {
  try {
    const data = JSON.parse((e && e.postData && e.postData.contents) || "{}");
    if (SECRET && data.secret !== SECRET) {
      return ContentService.createTextOutput(
        JSON.stringify({ ok: false, error: "unauthorized" })
      ).setMimeType(ContentService.MimeType.JSON);
    }

    const ss = SpreadsheetApp.openById(SHEET_ID);
    let sh = ss.getSheetByName(SHEET_NAME);
    if (!sh) {
      sh = ss.insertSheet(SHEET_NAME);
      sh.appendRow([
        "Timestamp",
        "Name",
        "Phone",
        "Category",
        "Day",
        "Day label",
        "Meal",
        "Meal label",
        "Action",
        "Token",
      ]);
    }

    sh.appendRow([
      data.timestamp || "",
      data.name || "",
      data.phone || "",
      data.category || "",
      data.day || "",
      data.day_label || "",
      data.meal || "",
      data.meal_label || "",
      data.action || "",
      data.token || "",
    ]);

    return ContentService.createTextOutput(
      JSON.stringify({ ok: true })
    ).setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService.createTextOutput(
      JSON.stringify({ ok: false, error: String(err) })
    ).setMimeType(ContentService.MimeType.JSON);
  }
}
