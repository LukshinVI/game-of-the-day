import {
    feedbackCases
} from './feedbackCases.js';

import {
    analyzeFeedback
} from '../src/agents/feedbackAgent.js';

import {
    isGrounded
} from '../src/agents/feedbackGrounding.js';

export async function runFeedbackEvals(
    env,
    caseId = null
) {
    const selectedCases =
        caseId
            ? feedbackCases.filter(
                (testCase) =>
                    testCase.id ===
                    caseId
            )
            : feedbackCases;

    if (
        caseId &&
        selectedCases.length === 0
    ) {
        throw new Error(
            `EVAL_CASE_NOT_FOUND: ${caseId}`
        );
    }

    const results = [];

    for (
        const testCase of
        selectedCases
    ) {
        try {
            const actual =
                await analyzeFeedback(
                    env,
                    testCase.input
                );

            const checks =
                evaluateCase(
                    testCase,
                    actual
                );

            results.push({
                id:
                    testCase.id,

                description:
                    testCase.description,

                passed:
                    Object.values(
                        checks
                    ).every(Boolean),

                checks,

                expected:
                    testCase.expected,

                actual
            });
        } catch (error) {
            results.push({
                id:
                    testCase.id,

                description:
                    testCase.description,

                passed: false,

                error:
                    error.message,

                actual:
                    error.feedbackResult ??
                    null
            });
        }
    }

    return results;
}

function evaluateCase(
    testCase,
    actual
) {
    const expected =
        testCase.expected;

    const positive =
        actual.signals.filter(
            (signal) =>
                signal.reaction_status ===
                    'CLEAR' &&
                signal.polarity ===
                    'POSITIVE'
        );

    const negative =
        actual.signals.filter(
            (signal) =>
                signal.reaction_status ===
                    'CLEAR' &&
                signal.polarity ===
                    'NEGATIVE'
        );

    const ambiguous =
        actual.signals.filter(
            (signal) =>
                signal.reaction_status ===
                    'AMBIGUOUS'
        );

    return {
        decision:
            actual.decision ===
            expected.decision,

        positive:
            positive.length ===
            expected.positive,

        negative:
            negative.length ===
            expected.negative,

        ambiguous:
            expected.ambiguous ===
                0
                ? ambiguous.length ===
                    0
                : ambiguous.length >=
                    expected.ambiguous,

        clarification:
            actual.clarification
                .required ===
            expected.clarification,

        clarificationType:
            expected
                .clarificationType ===
                undefined ||
            actual.clarification
                .type ===
                expected
                    .clarificationType,

        negativeReason:
            expected
                .negativeReason ===
                undefined ||
            negative.some(
                (signal) =>
                    signal
                        .negative_reason ===
                    expected
                        .negativeReason
            ),

        evidenceType:
            expected.evidenceType ===
                undefined ||
            actual.signals.some(
                (signal) =>
                    signal.evidence_type ===
                    expected.evidenceType
            ),

        targetContains:
            checkTargetContains(
                actual.signals,
                expected.targetContains
            ),

        targetStatus:
            checkTargetStatus(
                actual.signals,
                expected.targetStatus
            ),

        forbiddenTargets:
            checkForbiddenTargets(
                actual.signals,
                expected
                    .forbiddenTargets ??
                    []
            ),

        provenance:
            checkProvenance(
                actual.signals,
                testCase.input
                    .feedback.text
            ),

        maxModelCalls:
            actual.meta.model_calls <= 2
    };
}

function checkTargetContains(
    signals,
    expected
) {
    if (!expected) {
        return true;
    }

    const fragment =
        expected.toLowerCase();

    return signals.some(
        (signal) =>
            typeof signal.target
                ?.target_text ===
                'string' &&
            signal.target
                .target_text
                .toLowerCase()
                .includes(fragment)
    );
}

function checkTargetStatus(
    signals,
    expected
) {
    if (!expected) {
        return true;
    }

    return signals.some(
        (signal) =>
            signal.target?.status ===
            expected
    );
}

function checkForbiddenTargets(
    signals,
    forbidden
) {
    if (forbidden.length === 0) {
        return true;
    }

    const targets =
        signals.flatMap(
            (signal) => [
                signal.target
                    ?.target_text,

                ...(
                    signal.target
                        ?.candidate_target_texts ??
                    []
                )
            ]
        )
            .filter(
                (value) =>
                    typeof value ===
                    'string'
            )
            .map(
                (value) =>
                    value.toLowerCase()
            );

    return forbidden.every(
        (item) =>
            !targets.some(
                (target) =>
                    target.includes(
                        item.toLowerCase()
                    )
            )
    );
}

function checkProvenance(
    signals,
    feedbackText
) {
    for (
        const signal of signals
    ) {
        if (
            !isGrounded(
                feedbackText,
                signal.evidence_text
            )
        ) {
            return false;
        }

        if (
            !isGrounded(
                feedbackText,
                signal.reason_text
            )
        ) {
            return false;
        }

        if (
            !isGrounded(
                feedbackText,
                signal.target
                    ?.target_text ??
                    null
            )
        ) {
            return false;
        }

        for (
            const candidate of
            signal.target
                ?.candidate_target_texts ??
                []
        ) {
            if (
                !isGrounded(
                    feedbackText,
                    candidate
                )
            ) {
                return false;
            }
        }
    }

    return true;
}