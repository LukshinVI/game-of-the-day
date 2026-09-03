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

            const checks = {
                decision:
                    actual.decision === testCase.expected.decision,

                liked:
                    (actual.liked_mechanics.length > 0) ===
                    testCase.expected.liked,

                disliked:
                    (actual.disliked_mechanics.length > 0) ===
                    testCase.expected.disliked
            };

            const passed = Object.values(checks).every(Boolean);

            results.push({
                id: testCase.id,
                description: testCase.description,
                passed,
                checks,
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