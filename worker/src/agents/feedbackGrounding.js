export function resolveEvidence(
    feedbackText,
    evidence
) {
    if (
        typeof evidence !== 'string' ||
        evidence.trim() === ''
    ) {
        return null;
    }

    if (feedbackText.includes(evidence)) {
        return evidence;
    }

    return findNormalizedEvidence(
        feedbackText,
        evidence
    );
}

export function isGrounded(
    feedbackText,
    value
) {
    return (
        value === null ||
        (
            typeof value === 'string' &&
            feedbackText.includes(value)
        )
    );
}

function findNormalizedEvidence(
    feedbackText,
    evidence
) {
    const sourceWords =
        tokenizeWithOffsets(feedbackText);

    const evidenceWords =
        tokenize(evidence);

    if (evidenceWords.length === 0) {
        return null;
    }

    const normalizedEvidence =
        evidenceWords.map(normalizeWord);

    const candidates = [];

    for (
        let start = 0;
        start <=
            sourceWords.length -
            evidenceWords.length;
        start += 1
    ) {
        const window =
            sourceWords.slice(
                start,
                start + evidenceWords.length
            );

        const matches =
            window.every(
                (word, index) =>
                    normalizeWord(word.text) ===
                    normalizedEvidence[index]
            );

        if (!matches) {
            continue;
        }

        candidates.push({
            start: window[0].start,
            end:
                window[
                    window.length - 1
                ].end
        });
    }

    if (candidates.length !== 1) {
        return null;
    }

    return feedbackText.slice(
        candidates[0].start,
        candidates[0].end
    );
}

function tokenize(text) {
    return (
        text.match(
            /[\p{L}\p{N}]+/gu
        ) ?? []
    );
}

function tokenizeWithOffsets(text) {
    const words = [];
    const regex =
        /[\p{L}\p{N}]+/gu;

    let match;

    while (
        (
            match =
                regex.exec(text)
        ) !== null
    ) {
        words.push({
            text: match[0],
            start: match.index,
            end:
                match.index +
                match[0].length
        });
    }

    return words;
}

function normalizeWord(word) {
    return word.toLocaleLowerCase(
        'ru-RU'
    );
}