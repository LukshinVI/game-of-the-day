// Reads the JSON report of /__evals/feedback from stdin, prints a summary and
// exits non-zero if any case failed. Used by the manual LLM-evals workflow.
import { appendFileSync, readFileSync } from 'node:fs';

const report = JSON.parse(readFileSync(0, 'utf8'));

if (report.error) {
    console.error(`Eval run failed: ${report.error}`);
    process.exit(1);
}

const results = report.results ?? [];
const failed = results.filter((result) => !result.passed);

console.log(`model: ${report.model} (${report.model_override ?? 'production default'})`);
console.log(`cases: ${results.length}, passed: ${results.length - failed.length}, failed: ${failed.length}`);

for (const result of results) {
    console.log(`${result.passed ? 'PASS' : 'FAIL'}  ${result.id}${result.error ? `  (${result.error})` : ''}`);
}

if (process.env.GITHUB_STEP_SUMMARY) {
    const rows = results
        .map((result) => `| ${result.passed ? '✅' : '❌'} | \`${result.id}\` | ${result.description ?? ''} |`)
        .join('\n');

    const summary = [
        `### LLM evals: ${report.model}`,
        '',
        `${results.length - failed.length}/${results.length} passed`,
        '',
        '| | case | description |',
        '|---|---|---|',
        rows,
        ''
    ].join('\n');

    appendFileSync(process.env.GITHUB_STEP_SUMMARY, summary);
}

if (results.length === 0 || failed.length > 0) {
    process.exit(1);
}
