# Legacy Google Apps Script sender

The first version of Game of the Day: a Google Apps Script that read the day's
game from a Google Sheet and sent it through the VK API.

It is kept for history and as the documented rollback path during the migration
to the host/SQLite runtime (see [Architecture](../../docs/architecture.md)).
It is not part of the current delivery pipeline and is not covered by CI.

Deployment-specific values (`<SPREADSHEET_ID>`, `VK_TOKEN`, `VK_PEER_ID`) live in
the Apps Script project, not in this repository.
