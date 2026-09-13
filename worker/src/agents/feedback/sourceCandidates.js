const TOKEN_PATTERN =
    /[\p{L}\p{N}]+/gu;

const MAX_NGRAM_TOKENS = 4;

export function buildSourceCandidates(
    feedbackText
) {
    if (
        typeof feedbackText !== 'string' ||
        feedbackText.trim().length === 0
    ) {
        return [];
    }

    const tokens =
        collectTokens(
            feedbackText
        );

    const rawCandidates = [];

    for (
        let start = 0;
        start < tokens.length;
        start += 1
    ) {
        for (
            let length = 1;
            length <=
                MAX_NGRAM_TOKENS &&
            start + length <=
                tokens.length;
            length += 1
        ) {
            const end =
                start + length - 1;

            const startOffset =
                tokens[start].start;

            const endOffset =
                tokens[end].end;

            const text =
                feedbackText.slice(
                    startOffset,
                    endOffset
                );

            rawCandidates.push({
                text,
                start_offset:
                    startOffset,
                end_offset:
                    endOffset,
                token_count:
                    length
            });
        }
    }

    const uniqueCandidates =
        deduplicateCandidates(
            rawCandidates
        );

    return uniqueCandidates.map(
        (candidate, index) => ({
            candidate_id:
                `candidate-${index + 1}`,

            ...candidate
        })
    );
}

export function findCandidateById(
    candidates,
    candidateId
) {
    return (
        candidates.find(
            (candidate) =>
                candidate.candidate_id ===
                candidateId
        ) || null
    );
}

function collectTokens(
    feedbackText
) {
    const tokens = [];

    for (
        const match of
        feedbackText.matchAll(
            TOKEN_PATTERN
        )
    ) {
        tokens.push({
            text:
                match[0],

            start:
                match.index,

            end:
                match.index +
                match[0].length
        });
    }

    return tokens;
}

function deduplicateCandidates(
    candidates
) {
    const seen =
        new Set();

    const result = [];

    for (
        const candidate of
        candidates
    ) {
        const key =
            [
                candidate.start_offset,
                candidate.end_offset
            ].join(':');

        if (
            seen.has(key)
        ) {
            continue;
        }

        seen.add(key);
        result.push(candidate);
    }

    return result;
}