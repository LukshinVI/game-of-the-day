const GENERIC_TARGETS =
    new Set([
        'действие',
        'действия',
        'игра',
        'игру',
        'игрой',
        'это',
        'этого',
        'этому',
        'этим',
        'такое',
        'такого',
        'такому',
        'таким'
    ]);

export function containsEvidence(
    candidate,
    evidence
) {
    if (
        typeof candidate !== 'string' ||
        typeof evidence !== 'string' ||
        evidence.length === 0
    ) {
        return false;
    }

    return normalizeText(
        candidate
    ).includes(
        normalizeText(
            evidence
        )
    );
}

export function isContainedInEvidence(
    candidate,
    evidence
) {
    if (
        typeof candidate !== 'string' ||
        typeof evidence !== 'string' ||
        candidate.length === 0 ||
        evidence.length === 0
    ) {
        return false;
    }

    return normalizeText(
        evidence
    ).includes(
        normalizeText(
            candidate
        )
    );
}

export function isGenericTarget(
    candidateText
) {
    if (
        typeof candidateText !==
            'string'
    ) {
        return true;
    }

    return GENERIC_TARGETS.has(
        normalizeText(
            candidateText
        )
    );
}

/*
 * A target may legitimately be a smaller
 * fragment inside its own evidence:
 *
 *   "попросил повторить песенку"
 *                    ↓
 *                 "песенку"
 *
 * But a fragment that covers almost the
 * entire evidence is usually not a target.
 * It is just a shortened restatement of
 * the reaction / behavior itself.
 *
 * Example:
 *
 * evidence:
 *   "раз сам повторял действие"
 *
 * candidate:
 *   "сам повторял действие"
 *
 * 3 of 4 evidence tokens are copied.
 * This is not a useful semantic target.
 */
export function isNearRestatementOfEvidence(
    candidateText,
    evidenceText
) {
    const candidateTokens =
        tokenize(
            candidateText
        );

    const evidenceTokens =
        tokenize(
            evidenceText
        );

    if (
        candidateTokens.length < 2 ||
        evidenceTokens.length < 2
    ) {
        return false;
    }

    const longestRun =
        longestCommonContiguousRun(
            candidateTokens,
            evidenceTokens
        );

    /*
     * Require the candidate itself to be
     * almost entirely copied from evidence
     * and to cover at least 75% of that
     * evidence.
     *
     * This deliberately leaves small,
     * compact target spans available.
     */
    const candidateCoverage =
        longestRun /
        candidateTokens.length;

    const evidenceCoverage =
        longestRun /
        evidenceTokens.length;

    return (
        longestRun >= 2 &&
        candidateCoverage >= 0.75 &&
        evidenceCoverage >= 0.75
    );
}

/*
 * Evidence belonging to another signal must
 * not become the target of the current one.
 *
 * Exact substring checks are not enough.
 *
 * Example:
 *
 * other evidence:
 *   "раз сам повторял действие"
 *
 * candidate:
 *   "несколько раз сам повторял"
 *
 * They are not literal substrings of each
 * other, but they share the same long
 * semantic span.
 */
export function substantiallyOverlapsEvidence(
    candidateText,
    evidenceText
) {
    const candidateTokens =
        tokenize(
            candidateText
        );

    const evidenceTokens =
        tokenize(
            evidenceText
        );

    if (
        candidateTokens.length < 2 ||
        evidenceTokens.length < 2
    ) {
        return false;
    }

    const longestRun =
        longestCommonContiguousRun(
            candidateTokens,
            evidenceTokens
        );

    if (
        longestRun < 2
    ) {
        return false;
    }

    const candidateCoverage =
        longestRun /
        candidateTokens.length;

    const evidenceCoverage =
        longestRun /
        evidenceTokens.length;

    return (
        candidateCoverage >= 0.75 &&
        evidenceCoverage >= 0.5
    );
}

export function overlapsOtherSignalEvidence(
    candidateText,
    currentSignalId,
    allSignals
) {
    return allSignals.some(
        (signal) => {
            if (
                signal.signal_id ===
                    currentSignalId ||
                typeof signal.evidence_text !==
                    'string' ||
                signal.evidence_text.length ===
                    0
            ) {
                return false;
            }

            /*
             * First handle direct containment.
             */
            if (
                isContainedInEvidence(
                    candidateText,
                    signal.evidence_text
                )
            ) {
                return true;
            }

            /*
             * Then handle near-overlap where
             * wording extends slightly beyond
             * the other evidence span.
             */
            return substantiallyOverlapsEvidence(
                candidateText,
                signal.evidence_text
            );
        }
    );
}

/*
 * Kept as a compatibility boundary for any
 * code still importing the old helper.
 *
 * Its semantics remain specifically about
 * BEHAVIOR_ONLY evidence.
 */
function longestCommonContiguousRun(
    leftTokens,
    rightTokens
) {
    let previousRow =
        new Array(
            rightTokens.length + 1
        ).fill(0);

    let longest = 0;

    for (
        let leftIndex = 1;
        leftIndex <=
            leftTokens.length;
        leftIndex += 1
    ) {
        const currentRow =
            new Array(
                rightTokens.length + 1
            ).fill(0);

        for (
            let rightIndex = 1;
            rightIndex <=
                rightTokens.length;
            rightIndex += 1
        ) {
            if (
                leftTokens[
                    leftIndex - 1
                ] ===
                rightTokens[
                    rightIndex - 1
                ]
            ) {
                currentRow[
                    rightIndex
                ] =
                    previousRow[
                        rightIndex - 1
                    ] + 1;

                longest =
                    Math.max(
                        longest,
                        currentRow[
                            rightIndex
                        ]
                    );
            }
        }

        previousRow =
            currentRow;
    }

    return longest;
}

function tokenize(
    value
) {
    if (
        typeof value !==
            'string'
    ) {
        return [];
    }

    return (
        normalizeText(
            value
        ).match(
            /[\p{L}\p{N}]+/gu
        ) ?? []
    );
}

function normalizeText(
    value
) {
    return value
        .trim()
        .toLocaleLowerCase(
            'ru-RU'
        )
        .replace(
            /\s+/g,
            ' '
        );
}