function doPost(e) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName("Accepted") || ss.getActiveSheet();
  var data = JSON.parse(e.postData.contents);

  var headers = data.headers;
  var values = data.values;

  // Backwards compatibility with the legacy payload schema
  if (!headers || !values) {
    headers = ["Company Name", "Website", "Company Email", "Location", "Services"];
    values = {
      "Company Name": data.company_name || "",
      "Website": data.source_url || "",
      "Company Email": data.email || "Contact Form Only",
      "Location": data.location || "",
      "Services": data.services || ""
    };
  }

  var lastRow = sheet.getLastRow();
  var lastCol = sheet.getLastColumn();

  // If the sheet is empty, write headers and values
  if (lastRow === 0) {
    sheet.appendRow(headers);
    var rowValues = [];
    for (var i = 0; i < headers.length; i++) {
      rowValues.push(values[headers[i]] || "");
    }
    sheet.appendRow(rowValues);
  } else {
    // Read existing headers from the first row
    var sheetHeaders = sheet.getRange(1, 1, 1, lastCol).getValues()[0];
    var headerIndexMap = {};
    for (var i = 0; i < sheetHeaders.length; i++) {
      headerIndexMap[sheetHeaders[i].toString().trim()] = i + 1;
    }

    // Check if there are any new headers in the request that are not in the sheet
    var newHeadersAdded = false;
    for (var j = 0; j < headers.length; j++) {
      var h = headers[j].toString().trim();
      if (!headerIndexMap.hasOwnProperty(h)) {
        lastCol++;
        sheet.getRange(1, lastCol).setValue(h);
        headerIndexMap[h] = lastCol;
        newHeadersAdded = true;
      }
    }

    // Construct the row values to write matching current headers (which may have expanded)
    var valuesToWrite = [];
    for (var k = 0; k < lastCol; k++) {
      valuesToWrite.push("");
    }

    // Populate valuesToWrite based on header mapping
    for (var key in values) {
      var keyTrimmed = key.toString().trim();
      if (headerIndexMap.hasOwnProperty(keyTrimmed)) {
        var idx = headerIndexMap[keyTrimmed] - 1;
        valuesToWrite[idx] = values[key];
      }
    }

    sheet.appendRow(valuesToWrite);
  }

  return ContentService
    .createTextOutput(JSON.stringify({ ok: true, sheet: "Accepted" }))
    .setMimeType(ContentService.MimeType.JSON);
}
