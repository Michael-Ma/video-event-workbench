import { describe, expect, it } from 'vitest';
import { artifactRefs, artifactUrl, eventStart, formatTime, locationLabel, taskRange } from './utils';

describe('source time and trace helpers', () => {
  it('formats integer microseconds without treating unknown as zero', () => {
    expect(formatTime(null, true)).toBe('未知');
    expect(formatTime(3_661_250_000, true)).toBe('01:01:01.250');
    expect(formatTime(59_999_600, true)).toBe('01:00.000');
  });
  it('does not turn point uncertainty into an interval event', () => {
    const point = { kind: 'point' as const, anchor_us: null, anchor_range_us: [900_000, 1_100_000] as [number, number] };
    expect(locationLabel(point)).toBe('未知');
    expect(eventStart(point)).toBe(900_000);
  });
  it('uses responsible core ranges, not context read ranges', () => {
    expect(taskRange({ task_id: '1', stage: 'scan', status: 'succeeded', window: { core_start_us: 10, core_end_us: 20, read_start_us: 0, read_end_us: 30 } })).toEqual([10, 20]);
  });
  it('only links local artifact references and encodes path segments', () => {
    expect(artifactUrl('runs/a/response one.json')).toBe('/api/artifacts/runs/a/response%20one.json');
    expect(artifactUrl('../secret')).toBeNull();
    expect(artifactUrl('https://external.example')).toBeNull();
    expect(artifactRefs({ artifact_refs: ['runs/a/request.json'], details: { response_path: 'runs/a/response.json', raw_text: 'ignore' } })).toEqual(['runs/a/request.json', 'runs/a/response.json']);
  });
});
