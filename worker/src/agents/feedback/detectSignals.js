import {
    segmentFeedback
} from './segmentation/segmentFeedback.js';

import {
    runDetectionModel
} from './detection/runDetectionModel.js';

import {
    normalizeDetectionResponse
} from './detection/normalizeDetectionResponse.js';

import {
    validateDetectionResult
} from './detection/validateDetectionResult.js';

import {
    resolveDetectionSignals
} from './detection/resolveDetectionSignals.js';

import {
    logDetectionFailure
} from './detection/diagnostics.js';

export async function detectSignals(
    env,
    model,
    feedbackText
) {
    const {
        segments,
        relations
    } =
        segmentFeedback(
            feedbackText
        );

    if (
        segments.length ===
        0
    ) {
        return [];
    }

    const response =
        await runDetectionModel(
            env,
            model,
            segments,
            relations
        );

    /*
     * Pass the COMPLETE provider response.
     *
     * normalizeDetectionResponse owns
     * provider-envelope normalization.
     *
     * Semantic validation remains fully
     * provider-independent.
     */
    const result =
        normalizeDetectionResponse(
            response
        );

    try {
        validateDetectionResult(
            result,
            segments,
            relations
        );
    } catch (error) {
        logDetectionFailure(
            error,
            response,
            result
        );

        throw error;
    }

    return resolveDetectionSignals(
        result.classifications,
        segments,
        relations
    );
}