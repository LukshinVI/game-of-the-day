function sendGameOfTheDay() {
    const props = PropertiesService.getScriptProperties();
    const token = props.getProperty('VK_TOKEN');
    const peerId = props.getProperty('VK_PEER_ID');

    const spreadsheetId = '<SPREADSHEET_ID>';
    const sheet = SpreadsheetApp
        .openById(spreadsheetId)
        .getSheetByName('Журнал игр');


    let gameRow;

    try {
        gameRow = findTodayGameRow(sheet);
    } catch (error) {
        console.log(`Ошибка: ${error.message}. Отправка отменена.`);
        return;
    }

    if (gameRow === null) {
        console.log('Сегодняшняя игра ещё не появилась в журнале.');
        return;
    }

    // I = готовый текст, J = статус отправки
    const message = sheet.getRange(gameRow, 9).getValue();
    const status = sheet.getRange(gameRow, 10).getValue();

    if (!message) {
        console.log('В сегодняшней строке нет текста для отправки.');
        return;
    }

    if (status === 'Да') {
        console.log('Эта игра уже была отправлена.');
        return;
    }

    const response = UrlFetchApp.fetch(
        'https://api.vk.com/method/messages.send',
        {
            method: 'post',
            payload: {
                peer_id: peerId,
                message: message,
                random_id: String(Math.floor(Math.random() * 2147483647)),
                access_token: token,
                v: '5.199'
            }
        }
    );

    const data = JSON.parse(response.getContentText());

    if (data.error) {
        console.log('VK error: ' + JSON.stringify(data.error));
        return;
    }

    // Ставим отметку только после успешного ответа VK
    sheet.getRange(gameRow, 10).setValue('Да');

    console.log('Игра успешно отправлена в VK.');
}

function findTodayGameRow(sheet) {
    const lastRow = sheet.getLastRow();

    if (lastRow < 2) {
        return null;
    }

    const today = Utilities.formatDate(
        new Date(),
        'Europe/Moscow',
        'dd.MM.yyyy'
    );

    const todayRows = [];

    for (let row = lastRow; row >= 2; row--) {
        const rowDate = sheet.getRange(row, 2).getDisplayValue().trim();

        if (rowDate === today) {
            todayRows.push(row);
            continue;
        }

        break;
    }

    if (todayRows.length === 0) {
        return null;
    }

    if (todayRows.length > 1) {
        throw new Error(
            `Найдено несколько игр за сегодня: ${todayRows.length}`
        );
    }

    return todayRows[0];
}
