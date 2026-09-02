function sendGameOfTheDay() {
  const props = PropertiesService.getScriptProperties();
  const token = props.getProperty('VK_TOKEN');
  const peerId = props.getProperty('VK_PEER_ID');

  const spreadsheetId = '<SPREADSHEET_ID>';
  const sheet = SpreadsheetApp
    .openById(spreadsheetId)
    .getSheetByName('Журнал игр');

  
const lastRow = sheet.getLastRow();

if (lastRow < 2) {
  console.log('В журнале пока нет игр.');
  return;
}

const rowDate = sheet.getRange(lastRow, 2).getDisplayValue().trim();

const today = Utilities.formatDate(
  new Date(),
  'Europe/Moscow',
  'dd.MM.yyyy'
);

if (rowDate !== today) {
  console.log('Сегодняшняя игра ещё не появилась в журнале.');
  return;
}

  // I = готовый текст, J = статус отправки
  const message = sheet.getRange(lastRow, 9).getValue();
  const status = sheet.getRange(lastRow, 10).getValue();

  if (!message) {
    console.log('В последней строке нет текста для отправки.');
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
  sheet.getRange(lastRow, 10).setValue('Да');

  console.log('Игра успешно отправлена в VK.');
}