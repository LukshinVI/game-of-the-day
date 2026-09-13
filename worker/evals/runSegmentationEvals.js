import {
    segmentFeedback
} from '../src/agents/feedback/segmentation/segmentFeedback.js';

const cases = [
    {
        id:
            'generic-positive',

        input:
            'Ребёнку понравилось, несколько раз сам повторял действие',

        expectedSegments: [
            'Ребёнку понравилось',
            'несколько раз сам повторял действие'
        ],

        expectedRelations: []
    },

    {
        id:
            'negative-difficulty',

        input:
            'Расстроился и заплакал, потому что башня всё время падала и не получалось её построить',

        expectedSegments: [
            'Расстроился и заплакал',
            'потому что башня всё время падала и не получалось её построить'
        ],

        expectedRelations: [
            {
                type:
                    'CAUSE',

                from:
                    'segment-2',

                to:
                    'segment-1'
            }
        ]
    },

    {
        id:
            'behavioral-dislike',

        input:
            'Пару раз толкнул мяч, потом отвернулся и больше к нему не возвращался',

        expectedSegments: [
            'Пару раз толкнул мяч',
            'потом отвернулся и больше к нему не возвращался'
        ],

        expectedRelations: []
    },

    {
        id:
            'contrast',

        input:
            'Сначала заинтересовался, но потом отвернулся',

        expectedSegments: [
            'Сначала заинтересовался',
            'но потом отвернулся'
        ],

        expectedRelations: [
            {
                type:
                    'CONTRAST',

                from:
                    'segment-1',

                to:
                    'segment-2'
            }
        ]
    }
];

let failed = 0;

for (
    const testCase of
    cases
) {
    const actual =
        segmentFeedback(
            testCase.input
        );

    const actualSegments =
        actual.segments.map(
            (segment) =>
                segment.text
        );

    const actualRelations =
        actual.relations.map(
            (relation) => ({
                type:
                    relation.type,

                from:
                    relation
                        .from_segment_id,

                to:
                    relation
                        .to_segment_id
            })
        );

    const segmentsPassed =
        JSON.stringify(
            actualSegments
        ) ===
        JSON.stringify(
            testCase
                .expectedSegments
        );

    const relationsPassed =
        JSON.stringify(
            actualRelations
        ) ===
        JSON.stringify(
            testCase
                .expectedRelations
        );

    const passed =
        segmentsPassed &&
        relationsPassed;

    if (!passed) {
        failed += 1;
    }

    console.log(
        JSON.stringify(
            {
                id:
                    testCase.id,

                passed,

                expected: {
                    segments:
                        testCase
                            .expectedSegments,

                    relations:
                        testCase
                            .expectedRelations
                },

                actual: {
                    segments:
                        actualSegments,

                    relations:
                        actualRelations
                }
            },
            null,
            2
        )
    );
}

if (failed > 0) {
    process.exitCode = 1;
}