const FEEDBACK_SPREADSHEET_ID =
    '<SPREADSHEET_ID>';

const FEEDBACK_SHEET_NAME = 'Журнал игр';

/**
 * HTTP entrypoint для Cloudflare Worker.
 */
function doPost(e) {
    try {
        const request = parseFeedbackRequest(e);

        validateFeedbackApiSecret(request.secret);

        switch (request.action) {
            case 'save_raw':
                return jsonResponse(
                    saveRawFeedback(request)
                );

            case 'save_analysis':
                return jsonResponse(
                    saveFeedbackAnalysis(request)
                );

            default:
                throw new Error('UNKNOWN_ACTION');
        }
    } catch (error) {
        console.log(
            'Feedback API error: ' +
            error.message
        );

        return jsonResponse({
            ok: false,
            error: error.message
        });
    }
}

/**
 * Временный локальный smoke-test.
 *
 * Запускается вручную из Apps Script Editor.
 * Ничего в Google Sheets не пишет.
 */
function testFeedbackApiLocally() {
    const secret = PropertiesService
        .getScriptProperties()
        .getProperty('FEEDBACK_API_SECRET');

    if (!secret) {
        throw new Error(
            'FEEDBACK_API_SECRET_NOT_CONFIGURED'
        );
    }

    const e = {
        postData: {
            contents: JSON.stringify({
                action: 'unknown',
                secret
            })
        }
    };

    const response = doPost(e);

    const result = response.getContent();

    console.log(
        'Feedback API local test result:'
    );

    console.log(result);

    if (
        result !==
        '{"ok":false,"error":"UNKNOWN_ACTION"}'
    ) {
        throw new Error(
            'UNEXPECTED_TEST_RESULT: ' +
            result
        );
    }

    console.log(
        'Feedback API local test: PASS'
    );
}

function parseFeedbackRequest(e) {
    if (
        !e ||
        !e.postData ||
        !e.postData.contents
    ) {
        throw new Error('EMPTY_REQUEST');
    }

    let request;

    try {
        request = JSON.parse(
            e.postData.contents
        );
    } catch {
        throw new Error('INVALID_JSON');
    }

    if (
        !request ||
        typeof request !== 'object' ||
        Array.isArray(request)
    ) {
        throw new Error('INVALID_REQUEST');
    }

    return request;
}

function validateFeedbackApiSecret(
    actualSecret
) {
    const expectedSecret =
        PropertiesService
            .getScriptProperties()
            .getProperty(
                'FEEDBACK_API_SECRET'
            );

    if (!expectedSecret) {
        throw new Error(
            'FEEDBACK_API_SECRET_NOT_CONFIGURED'
        );
    }

    if (
        typeof actualSecret !== 'string' ||
        actualSecret !== expectedSecret
    ) {
        throw new Error('UNAUTHORIZED');
    }
}

function saveRawFeedback(request) {
    if (!request.eventId) {
        throw new Error(
            'EVENT_ID_REQUIRED'
        );
    }

    if (
        typeof request.text !==
            'string' ||
        request.text.trim() === ''
    ) {
        throw new Error(
            'FEEDBACK_TEXT_REQUIRED'
        );
    }

    const lock =
        LockService.getScriptLock();

    lock.waitLock(10000);

    try {
        const props =
            PropertiesService
                .getScriptProperties();

        const eventKey =
            buildFeedbackEventKey(
                request.eventId
            );

        const existingRow =
            props.getProperty(eventKey);

        /*
         * VK Callback может повторно
         * доставить один и тот же event.
         *
         * Не записываем одно сообщение
         * второй раз и не связываем
         * его с другой игрой.
         */
        if (existingRow) {
            const row =
                Number(existingRow);

            const sheet = getFeedbackSheet();

            return {
                ok: true,
                duplicate: true,
                processed: sheet
                    .getRange(row, 14)
                    .getDisplayValue()
                    .trim() === 'Да',
                row,
                game: getGameContext(
                    sheet,
                    row
                )
            };
        }

        const sheet =
            getFeedbackSheet();

        const row =
            findFeedbackTargetRow(
                sheet
            );

        if (row === null) {
            throw new Error(
                'FEEDBACK_TARGET_NOT_FOUND'
            );
        }

        const feedbackText =
            request.text.trim();

        /*
         * K = raw feedback
         * L = feedback time
         *
         * RAW сохраняется раньше AI.
         */
        sheet
            .getRange(row, 11)
            .setValue(
                feedbackText
            );

        sheet
            .getRange(row, 12)
            .setValue(
                new Date()
            );

        SpreadsheetApp.flush();

        /*
         * event_id → row
         *
         * Idempotency для VK Callback.
         */
        props.setProperty(
            eventKey,
            String(row)
        );

        return {
            ok: true,
            duplicate: false,
            row,
            game: getGameContext(
                sheet,
                row
            )
        };
    } finally {
        lock.releaseLock();
    }
}

function saveFeedbackAnalysis(
    request
) {
    const row =
        Number(request.row);

    if (
        !Number.isInteger(row) ||
        row < 2
    ) {
        throw new Error(
            'INVALID_ROW'
        );
    }

    if (
        !request.analysis ||
        typeof request.analysis !==
            'object' ||
        Array.isArray(
            request.analysis
        )
    ) {
        throw new Error(
            'ANALYSIS_REQUIRED'
        );
    }

    const sheet =
        getFeedbackSheet();

    if (
        row >
        sheet.getLastRow()
    ) {
        throw new Error(
            'ROW_NOT_FOUND'
        );
    }

    const rawFeedback =
        sheet
            .getRange(row, 11)
            .getDisplayValue()
            .trim();

    /*
     * Analysis нельзя сохранить,
     * если RAW ОС отсутствует.
     */
    if (!rawFeedback) {
        throw new Error(
            'RAW_FEEDBACK_MISSING'
        );
    }

    /*
     * M = результат ОС
     * N = ОС обработана
     */
    sheet
        .getRange(row, 13)
        .setValue(
            JSON.stringify(
                request.analysis
            )
        );

    sheet
        .getRange(row, 14)
        .setValue('Да');

    SpreadsheetApp.flush();

    return {
        ok: true,
        row,
        processed: true
    };
}

function findFeedbackTargetRow(
    sheet
) {
    const lastRow =
        sheet.getLastRow();

    if (lastRow < 2) {
        return null;
    }

    /*
     * MVP linking.
     *
     * Самая свежая строка:
     * J = Да
     * K = пусто
     */
    for (
        let row = lastRow;
        row >= 2;
        row--
    ) {
        const sent =
            sheet
                .getRange(row, 10)
                .getDisplayValue()
                .trim();

        const feedback =
            sheet
                .getRange(row, 11)
                .getDisplayValue()
                .trim();

        if (
            sent === 'Да' &&
            !feedback
        ) {
            return row;
        }
    }

    return null;
}

function getGameContext(
    sheet,
    row
) {
    return {
        id: String(
            sheet
                .getRange(row, 1)
                .getDisplayValue()
        ).trim(),

        title:
            sheet
                .getRange(row, 4)
                .getDisplayValue()
                .trim(),

        skill:
            sheet
                .getRange(row, 5)
                .getDisplayValue()
                .trim(),

        mechanic:
            sheet
                .getRange(row, 6)
                .getDisplayValue()
                .trim()
    };
}

function getFeedbackSheet() {
    const spreadsheet =
        SpreadsheetApp.openById(
            FEEDBACK_SPREADSHEET_ID
        );

    const sheet =
        spreadsheet.getSheetByName(
            FEEDBACK_SHEET_NAME
        );

    if (!sheet) {
        throw new Error(
            'FEEDBACK_SHEET_NOT_FOUND'
        );
    }

    return sheet;
}

function buildFeedbackEventKey(
    eventId
) {
    return (
        'VK_FEEDBACK_EVENT_' +
        String(eventId)
    );
}

function jsonResponse(data) {
    return ContentService
        .createTextOutput(
            JSON.stringify(data)
        )
        .setMimeType(
            ContentService.MimeType.JSON
        );
}
