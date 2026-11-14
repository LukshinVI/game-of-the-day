import assert from 'node:assert/strict';
import { withReactionLabel } from '../src/index.js';

assert.equal(withReactionLabel('', [{ type: 'sticker' }]), '[стикер]');
assert.equal(withReactionLabel('Понравилось', [{ type: 'photo' }, { type: 'photo' }]), 'Понравилось [фото]');
assert.equal(withReactionLabel('', [{ type: 'audio_message' }]), '[голосовое]');
assert.equal(withReactionLabel('', [{ type: 'wall' }]), '');
assert.equal(withReactionLabel('Текст', undefined), 'Текст');
assert.equal(withReactionLabel('', null), '');

console.log('reaction label tests: ok');
