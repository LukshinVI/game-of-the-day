import { feedbackCases } from './feedbackCases.js';
import { analyzeFeedback } from '../src/agents/feedbackAgent.js';

export async function runFeedbackEvals(env, caseId = null) {
    const results = [];

    const selectedCases = caseId
        ? feedbackCases.filter((testCase) => testCase.id === caseId)
        : feedbackCases;

    if (caseId && selectedCases.length === 0) {
        throw new Error(`EVAL_CASE_NOT_FOUND: ${caseId}`);
    }

    for (const testCase of selectedCases) {
        try {
            const actual = await analyzeFeedback(env, testCase.input);

            const positiveObservations = actual.observations.filter(
                (observation) => observation.reaction === 'POSITIVE'
            );

            const negativeObservations = actual.observations.filter(
                (observation) => observation.reaction === 'NEGATIVE'
            );

            const unresolvedObservations = actual.observations.filter(
                (observation) => observation.target === null
            );

            const targetChecks = checkTargets(
                actual.observations,
                testCase.expected.targetChecks ?? []
            );

            const checks = {
                decision:
                    actual.decision === testCase.expected.decision,

                positive:
                    positiveObservations.length ===
                    testCase.expected.observations.positive,

                negative:
                    negativeObservations.length ===
                    testCase.expected.observations.negative,

                unresolved:
                    unresolvedObservations.length ===
                    testCase.expected.observations.unresolved,

                targets:
                    targetChecks.passed
            };

            const passed = Object.values(checks).every(Boolean);

            results.push({
                id: testCase.id,
                description: testCase.description,
                passed,
                checks,
                targetChecks: targetChecks.details,
                expected: testCase.expected,
                actual
            });
        } catch (error) {
            results.push({
                id: testCase.id,
                description: testCase.description,
                passed: false,
                error: error.message,
                actual: error.feedbackResult ?? null
            });
        }
    }

    return results;
}

function checkTargets(observations, expectedChecks) {
    if (expectedChecks.length === 0) {
        return {
            passed: true,
            details: []
        };
    }

    const details = expectedChecks.map((expected) => {
        const expectedFragment = expected.contains.toLowerCase();

        const matched = observations.some((observation) => {
            if (observation.reaction !== expected.reaction) {
                return false;
            }

            if (typeof observation.target !== 'string') {
                return false;
            }

            return observation.target
                .toLowerCase()
                .includes(expectedFragment);
        });

        return {
            reaction: expected.reaction,
            contains: expected.contains,
            matched
        };
    });

    return {
        passed: details.every((check) => check.matched),
        details
    };
}