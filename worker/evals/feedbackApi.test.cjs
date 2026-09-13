const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = readFileSync(join(__dirname, '../src/feedbackApi.js'), 'utf8');

function fixture({ duplicate = true, processed = '' } = {}) {
    const cells = new Map([
        [1, '007'], [4, 'Game'], [5, 'Skill'], [6, 'Mechanic'],
        [10, 'Да'], [11, duplicate ? 'Original feedback' : ''],
        [14, processed]
    ]);
    const writes = [];
    let released = false;
    const sheet = {
        getLastRow: () => 2,
        getRange(row, column) {
            assert.equal(row, 2);
            return {
                getDisplayValue: () => String(cells.get(column) ?? ''),
                setValue(value) {
                    writes.push([column, value]);
                    cells.set(column, value);
                }
            };
        }
    };
    const context = vm.createContext({
        PropertiesService: {
            getScriptProperties: () => ({
                getProperty: () => duplicate ? '2' : null,
                setProperty: (key, value) => writes.push([key, value])
            })
        },
        LockService: {
            getScriptLock: () => ({
                waitLock() {},
                releaseLock() { released = true; }
            })
        },
        SpreadsheetApp: {
            openById: () => ({ getSheetByName: () => sheet }),
            flush() {}
        }
    });
    vm.runInContext(source, context);
    return { context, cells, writes, released: () => released };
}

for (const [marker, expected] of [['Да', true], [' Да ', true], ['', false], ['Нет', false]]) {
    test(`duplicate save_raw reports processed=${expected} for ${JSON.stringify(marker)}`, () => {
        const f = fixture({ processed: marker });
        const result = f.context.saveRawFeedback({ eventId: 'event', text: 'Retry' });
        assert.equal(result.ok, true);
        assert.equal(result.duplicate, true);
        assert.equal(result.processed, expected);
        assert.equal(result.row, 2);
        assert.equal(result.game.id, '007');
        assert.deepEqual(f.writes, []);
        assert.equal(f.released(), true);
    });
}

test('new save_raw retains response and raw persistence behavior', () => {
    const f = fixture({ duplicate: false });
    const result = f.context.saveRawFeedback({ eventId: 'event', text: ' New feedback ' });
    assert.equal(result.duplicate, false);
    assert.equal(Object.hasOwn(result, 'processed'), false);
    assert.equal(f.cells.get(11), 'New feedback');
    assert.equal(typeof f.cells.get(12).getTime(), 'number');
    assert.deepEqual(f.writes.at(-1), ['VK_FEEDBACK_EVENT_event', '2']);
    assert.equal(f.released(), true);
});

test('save_analysis retains persistence and processed response', () => {
    const f = fixture();
    const analysis = { observations: [], decision: 'NO_PREFERENCE' };
    const result = f.context.saveFeedbackAnalysis({ row: 2, analysis });
    assert.equal(result.ok, true);
    assert.equal(result.processed, true);
    assert.equal(f.cells.get(13), JSON.stringify(analysis));
    assert.equal(f.cells.get(14), 'Да');
});
