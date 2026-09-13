import {
    buildSourceCandidates
} from './sourceCandidates.js';

import {
    buildTargetContexts
} from './targetCandidates.js';

import {
    validateTargetResult
} from './targetValidation.js';

import {
    resolveTargetLinks,
    applyUnspecifiedTargets
} from './targetResolution.js';

import {
    normalizeTargetResponse
} from './targetResponse.js';

import {
    TARGET_SCHEMA
} from './schemas.js';

import {
    TARGET_LINKING_PROMPT
} from './prompts.js';

export async function linkTargets(
    env,
    model,
    feedbackText,
    allSignals,
    linkableSignals
) {
    const sourceCandidates =
        buildSourceCandidates(
            feedbackText
        );

    if (
        sourceCandidates.length ===
            0
    ) {
        return applyUnspecifiedTargets(
            allSignals,
            linkableSignals
        );
    }

    const signalContexts =
        buildTargetContexts(
            allSignals,
            linkableSignals,
            sourceCandidates
        );

    /*
     * Important free-tier optimization.
     *
     * If deterministic candidate generation
     * proves that there is no trustworthy
     * target candidate for ANY signal, there is
     * nothing useful for the model to decide.
     *
     * Do not spend a second AI inference merely
     * to ask the model to say UNSPECIFIED.
     */
    const hasAnyCandidate =
        signalContexts.some(
            (context) =>
                context.candidates.length >
                    0
        );

    if (!hasAnyCandidate) {
        return applyUnspecifiedTargets(
            allSignals,
            linkableSignals
        );
    }

    const rawResponse =
        await runTargetLinkingModel(
            env,
            model,
            feedbackText,
            signalContexts
        );

    const result =
        normalizeTargetResponse(
            rawResponse
        );

    /*
     * Structural failures still throw.
     *
     * Semantic target-selection violations are
     * normalized by validateTargetResult() to
     * UNSPECIFIED.
     */
    const validatedResult =
        validateTargetResult(
            result,
            signalContexts
        );

    return resolveTargetLinks(
        allSignals,
        validatedResult.links,
        sourceCandidates
    );
}

async function runTargetLinkingModel(
    env,
    model,
    feedbackText,
    signalContexts
) {
    return env.AI.run(
        model,
        {
            messages: [
                {
                    role:
                        'system',

                    content:
                        TARGET_LINKING_PROMPT
                },

                {
                    role:
                        'user',

                    content:
                        JSON.stringify({
                            feedback:
                                feedbackText,

                            signals:
                                signalContexts.map(
                                    ({
                                        signal,
                                        candidates
                                    }) => ({
                                        signal_id:
                                            signal.signal_id,

                                        evidence_type:
                                            signal.evidence_type,

                                        reaction_status:
                                            signal.reaction_status,

                                        polarity:
                                            signal.polarity,

                                        evidence_text:
                                            signal.evidence_text,

                                        source_candidates:
                                            candidates.map(
                                                (
                                                    candidate
                                                ) => ({
                                                    candidate_id:
                                                        candidate.candidate_id,

                                                    text:
                                                        candidate.text
                                                })
                                            )
                                    })
                                )
                        })
                }
            ],

            response_format: {
                type:
                    'json_schema',

                json_schema:
                    TARGET_SCHEMA
            },

            temperature:
                0
        }
    );
}