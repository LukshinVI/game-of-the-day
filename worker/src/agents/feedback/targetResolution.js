import {
    findCandidateById
} from './sourceCandidates.js';

import {
    containsEvidence,
    isGenericTarget,
    isNearRestatementOfEvidence,
    overlapsOtherSignalEvidence
} from './targetGuards.js';

export function resolveTargetLinks(
    signals,
    links,
    sourceCandidates
) {
    const linksBySignalId =
        new Map(
            links.map(
                (link) => [
                    link.signal_id,
                    link
                ]
            )
        );

    return signals.map(
        (signal) => {
            const link =
                linksBySignalId.get(
                    signal.signal_id
                );

            if (!link) {
                return signal;
            }

            return {
                ...signal,

                target:
                    resolveTarget(
                        link,
                        signal,
                        signals,
                        sourceCandidates
                    )
            };
        }
    );
}

export function applyUnspecifiedTargets(
    allSignals,
    linkableSignals
) {
    const linkableIds =
        new Set(
            linkableSignals.map(
                (signal) =>
                    signal.signal_id
            )
        );

    return allSignals.map(
        (signal) => {
            if (
                !linkableIds.has(
                    signal.signal_id
                )
            ) {
                return signal;
            }

            return {
                ...signal,

                target:
                    unspecifiedTarget()
            };
        }
    );
}

function resolveTarget(
    link,
    signal,
    allSignals,
    sourceCandidates
) {
    if (
        link.status ===
            'RESOLVED'
    ) {
        return resolveSingleTarget(
            link,
            signal,
            allSignals,
            sourceCandidates
        );
    }

    if (
        link.status ===
            'AMBIGUOUS'
    ) {
        return resolveAmbiguousTarget(
            link,
            signal,
            allSignals,
            sourceCandidates
        );
    }

    return unspecifiedTarget();
}

function resolveSingleTarget(
    link,
    signal,
    allSignals,
    sourceCandidates
) {
    const candidate =
        findCandidateById(
            sourceCandidates,
            link.candidate_ids[0]
        );

    if (
        !candidate ||
        !isValidResolvedCandidate(
            candidate.text,
            signal,
            allSignals
        )
    ) {
        return unspecifiedTarget();
    }

    return {
        status:
            'RESOLVED',

        target_text:
            candidate.text,

        candidate_target_texts:
            []
    };
}

function resolveAmbiguousTarget(
    link,
    signal,
    allSignals,
    sourceCandidates
) {
    const candidateTexts =
        link.candidate_ids
            .map(
                (candidateId) =>
                    findCandidateById(
                        sourceCandidates,
                        candidateId
                    )
            )
            .filter(Boolean)
            .map(
                (candidate) =>
                    candidate.text
            )
            .filter(
                (candidateText) =>
                    isValidResolvedCandidate(
                        candidateText,
                        signal,
                        allSignals
                    )
            );

    const uniqueCandidateTexts =
        [
            ...new Set(
                candidateTexts
            )
        ];

    if (
        uniqueCandidateTexts.length <
            2
    ) {
        return unspecifiedTarget();
    }

    return {
        status:
            'AMBIGUOUS',

        target_text:
            null,

        candidate_target_texts:
            uniqueCandidateTexts
    };
}

function isValidResolvedCandidate(
    candidateText,
    signal,
    allSignals
) {
    /*
     * Generic references such as
     * "действие" or "игра" do not identify
     * a useful semantic target.
     */
    if (
        isGenericTarget(
            candidateText
        )
    ) {
        return false;
    }

    /*
     * The target cannot contain the complete
     * reaction evidence.
     */
    if (
        containsEvidence(
            candidateText,
            signal.evidence_text
        )
    ) {
        return false;
    }

    /*
     * The target also cannot be merely a
     * shortened restatement of almost the
     * whole reaction / behavior evidence.
     */
    if (
        isNearRestatementOfEvidence(
            candidateText,
            signal.evidence_text
        )
    ) {
        return false;
    }

    /*
     * Final deterministic defense:
     * target evidence must not actually belong
     * to another semantic signal.
     */
    if (
        overlapsOtherSignalEvidence(
            candidateText,
            signal.signal_id,
            allSignals
        )
    ) {
        return false;
    }

    return true;
}

function unspecifiedTarget() {
    return {
        status:
            'UNSPECIFIED',

        target_text:
            null,

        candidate_target_texts:
            []
    };
}