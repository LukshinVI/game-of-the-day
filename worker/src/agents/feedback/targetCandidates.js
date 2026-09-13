import {
    isGenericTarget
} from './targetGuards.js';

export function buildTargetContexts(
    allSignals,
    linkableSignals,
    sourceCandidates
) {
    return linkableSignals.map(
        (signal) => ({
            signal,

            candidates:
                buildCandidatesForSignal(
                    signal,
                    allSignals,
                    sourceCandidates
                )
        })
    );
}

function buildCandidatesForSignal(
    signal,
    allSignals,
    sourceCandidates
) {
    /*
     * Target extraction is deliberately
     * conservative.
     *
     * Source candidates are excellent for
     * provenance because they enumerate exact
     * spans from the user's text.
     *
     * They are NOT automatically valid semantic
     * targets.
     *
     * Therefore each evidence type gets its own
     * candidate-space policy.
     */

    if (
        signal.evidence_type ===
            'VOLUNTARY_ENGAGEMENT'
    ) {
        return buildVoluntaryEngagementCandidates(
            signal,
            allSignals,
            sourceCandidates
        );
    }

    /*
     * For EXPLICIT_EVALUATION and AFFECT we
     * currently refuse to manufacture a target
     * from arbitrary words inside the reaction
     * evidence.
     *
     * Example:
     *
     *   "Ребёнку понравилось"
     *
     * "Ребёнку" is grounded text, but it is not
     * the thing that was liked.
     *
     * Until we have a stronger target extraction
     * contract, UNSPECIFIED is safer.
     */
    return [];
}

function buildVoluntaryEngagementCandidates(
    signal,
    allSignals,
    sourceCandidates
) {
    const evidenceText =
        normalizeText(
            signal.evidence_text
        );

    if (
        evidenceText.length === 0
    ) {
        return [];
    }

    /*
     * Current high-precision rule:
     *
     * In voluntary-engagement evidence, allow
     * only the final single-token span.
     *
     * Examples:
     *
     *   "попросил повторить песенку"
     *                       ↓
     *                  "песенку"
     *
     *   "сам повторял действие"
     *                  ↓
     *             "действие"
     *
     * The latter is then rejected as generic.
     *
     * We intentionally prefer false UNSPECIFIED
     * over storing a false target.
     */
    const terminalCandidates =
        sourceCandidates.filter(
            (candidate) =>
                candidate.token_count ===
                    1 &&
                isStrictTerminalSpan(
                    candidate.text,
                    evidenceText
                ) &&
                !isGenericTarget(
                    candidate.text
                ) &&
                !belongsToOtherSignal(
                    candidate.text,
                    signal.signal_id,
                    allSignals
                )
        );

    /*
     * Normally there will be one exact terminal
     * token. Deduplicate defensively by
     * normalized text.
     */
    const seen =
        new Set();

    return terminalCandidates.filter(
        (candidate) => {
            const key =
                normalizeText(
                    candidate.text
                );

            if (
                seen.has(key)
            ) {
                return false;
            }

            seen.add(key);

            return true;
        }
    );
}

function isStrictTerminalSpan(
    candidateText,
    normalizedEvidence
) {
    const candidate =
        normalizeText(
            candidateText
        );

    if (
        candidate.length === 0 ||
        normalizedEvidence.length ===
            0 ||
        candidate ===
            normalizedEvidence
    ) {
        return false;
    }

    return normalizedEvidence.endsWith(
        candidate
    );
}

function belongsToOtherSignal(
    candidateText,
    currentSignalId,
    allSignals
) {
    const candidate =
        normalizeText(
            candidateText
        );

    if (
        candidate.length === 0
    ) {
        return true;
    }

    return allSignals.some(
        (signal) => {
            if (
                signal.signal_id ===
                    currentSignalId ||
                typeof signal.evidence_text !==
                    'string'
            ) {
                return false;
            }

            const evidence =
                normalizeText(
                    signal.evidence_text
                );

            if (
                evidence.length ===
                    0
            ) {
                return false;
            }

            /*
             * If the complete candidate already
             * lives inside another independent
             * signal's evidence, do not borrow it
             * as the current signal's target.
             */
            return evidence.includes(
                candidate
            );
        }
    );
}

function normalizeText(
    value
) {
    if (
        typeof value !==
            'string'
    ) {
        return '';
    }

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