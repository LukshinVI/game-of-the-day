const CLAUSE_SEPARATORS =
    /[,;:.!?]+/gu;

export function splitClauses(
    feedbackText
) {
    if (
        typeof feedbackText !==
            'string' ||
        feedbackText.trim().length === 0
    ) {
        return [];
    }

    const clauses = [];

    let lastIndex = 0;

    for (
        const match of
        feedbackText.matchAll(
            CLAUSE_SEPARATORS
        )
    ) {
        const separatorIndex =
            match.index;

        pushClause(
            clauses,
            feedbackText,
            lastIndex,
            separatorIndex
        );

        lastIndex =
            separatorIndex +
            match[0].length;
    }

    pushClause(
        clauses,
        feedbackText,
        lastIndex,
        feedbackText.length
    );

    return clauses;
}

function pushClause(
    clauses,
    feedbackText,
    startOffset,
    endOffset
) {
    let start =
        startOffset;

    let end =
        endOffset;

    while (
        start < end &&
        /\s/u.test(
            feedbackText[start]
        )
    ) {
        start += 1;
    }

    while (
        end > start &&
        /\s/u.test(
            feedbackText[
                end - 1
            ]
        )
    ) {
        end -= 1;
    }

    if (start >= end) {
        return;
    }

    clauses.push({
        text:
            feedbackText.slice(
                start,
                end
            ),

        start_offset:
            start,

        end_offset:
            end
    });
}