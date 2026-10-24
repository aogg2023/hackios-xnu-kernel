#!/usr/bin/env python3
"""Check tools/frames.py against synthetic touch_lat=1 logs.

What it pins down: a stall of 100 ms or more stays in the segment's headline numbers (count,
percentiles, shares) and only leaves the secondary under-100 ms lines; a segment of two frames counts
its one gap like any other; the printed thresholds are the ones the code counts at (18.0 and 34.5 ms);
percentiles follow the nearest rank frames.py documents; the same lines read again from a second log of
the same run change nothing; a run without gestures, a gesture without frames and a frame the driver
showed without its hand-over print and exit well; the draw / gpu / idle / copy stages, the GPU waits
and the core loads keep their old shape.

Usage: python3 tools/test-frames.py
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

FRAMES = Path(__file__).resolve().parent / 'frames.py'


def steady(i, done):
    """A frame whose own stages cost little (0.5 ms of draw, 0.5 of GPU wait, 1 of copy): what the
    tests check sits in the gaps between frames."""
    return [f'tl: g {i} began {done - 9000} ended {done - 8500} 1',
            f'tl: s {i} at {done - 2000}',
            f'tl: v {i} ready {done - 1500} shown {done - 1000} done {done}']


def show(i, done, began, ended, swap, ready, shown):
    return [f'tl: g {i} began {began} ended {ended} 1',
            f'tl: s {i} at {swap}',
            f'tl: v {i} ready {ready} shown {shown} done {done}']


def run(lines):
    """frames.py over one log, and over a second file holding the same lines again (as the run's
    live log repeats them): the counts must not move."""
    with tempfile.TemporaryDirectory() as d:
        run_log = Path(d) / 'run.log'
        live_log = Path(d) / 'live.log'
        run_log.write_text('\n'.join(lines) + '\n')
        live_log.write_text('\n'.join(lines) + '\n')
        out = subprocess.run([sys.executable, str(FRAMES), str(run_log)], capture_output=True, text=True)
        assert out.returncode == 0, f'frames.py failed: {out.stderr}'
        once = out.stdout
        out = subprocess.run([sys.executable, str(FRAMES), str(run_log), str(live_log)],
                             capture_output=True, text=True)
        assert out.returncode == 0, f'frames.py over two logs failed: {out.stderr}'
        assert out.stdout == once, 'lines read again from the run\'s live log changed the counts'
    return once


def test_long_stalls_counted():
    # one touch, then frames 16, 20, 50, 100 and 500 ms apart; a button with no frame after it;
    # a frame the driver reported without its hand-over; and a line no number fits into
    lines = ['tl: p 1 0 32768 32768 at 1000000']
    for i, done in enumerate([1016000, 1032000, 1052000, 1102000, 1202000, 1702000]):
        lines += steady(100 + i, done)
    lines += ['tl: p 4 115 0 0 at 3500000',
              'tl: v 900 ready 4000000 shown 4001000 done 4002000',
              'tl: g 1 began x ended y 3']
    out = run(lines)
    for want in [
        "2 gestures, 6 frames copied into the panel's memory",
        '1 of the 7 frames the display driver reported are left out',
        'the hardware scanout and presentation are not measured',
        '5 steps; p50 50.0, p95 500.0, p99 500.0, max 500.0 ms (nearest rank)',
        '2 of them 100 ms or more',
        'intervals under 100 ms: 3 at 34.9 a second',
        'button 115: 0 frames',
        'the slow ones: draw  0.5, gpu  0.5, idle before 160.0, copy 1.0 ms',
        'no full split of the gap',
        'all gestures: 5 steps; p50 50.0, p95 500.0, p99 500.0, max 500.0 ms (nearest rank)',
    ]:
        assert want in out, f'missing {want!r} in:\n{out}'
    assert re.search(r'over 18\.0 ms\s+80%', out), f'the four gaps over 18.0 ms are not 80% of 5:\n{out}'
    assert re.search(r'over 34\.5 ms\s+60%', out), f'the three gaps over 34.5 ms are not 60% of 5:\n{out}'
    assert 'over 16.7' not in out and 'over 33 ms' not in out, f'an old threshold label survived:\n{out}'


def test_slow_frame_attribution():
    # five frames, one of them 40 ms after the previous. frames.py reports for that frame 20 ms of
    # drawing, 5 ms between the hand-over and the GPU being done, 10 ms before the window server
    # began it and 1 ms of copying: stages read at the frame's lines, covering part of the 40 ms,
    # not a split that adds up to it
    lines = ['tl: p 1 0 0 39321 at 1000000']
    for i, done in [(11, 1016000), (12, 1032000), (14, 1088000), (15, 1104000)]:
        lines += steady(i, done)
    lines += show(13, 1072000, 1040500, 1060500, 1065000, 1070000, 1071000)
    lines += ['tl: f EGL 5000 at 1030000', 'tl: u at 1050000 0:80 7:40']
    out = run(lines)
    for want in [
        '4 steps; p50 16.0, p95 40.0, p99 40.0, max 40.0 ms (nearest rank)',
        'the slow ones: draw 20.0, gpu  5.0, idle before  10.0, copy 1.0 ms',
        '1 waits for the GPU (5 ms)',
        'cores busy 7:40% 0:80%',
    ]:
        assert want in out, f'missing {want!r} in:\n{out}'
    assert re.search(r'over 18\.0 ms\s+25%', out), f'the one gap over 18.0 ms is not 25% of 4:\n{out}'
    assert 'intervals under 100 ms' not in out, f'a split this log never needed got printed:\n{out}'


def test_short_windows():
    # a segment of two frames 500 ms apart still counts its one gap; a segment of one frame says so;
    # two segments of the same touch aggregate, the one-frame one taking no part in it
    lines = ['tl: p 1 0 32768 32768 at 1000000']
    lines += steady(31, 1016000) + steady(32, 1516000)
    lines += ['tl: p 1 0 32768 32768 at 4000000']
    lines += steady(33, 4016000) + steady(34, 4032000)
    lines += ['tl: p 1 0 32768 32768 at 7000000'] + steady(35, 7016000)
    out = run(lines)
    for want in [
        '1 steps; p50 500.0, p95 500.0, p99 500.0, max 500.0 ms (nearest rank)',
        'intervals under 100 ms: 0',
        '7.000 s touch at 50%,50%: 1 frames',
        'the same gestures together:',
        '2 times, 2 steps; p50 16.0, p95 500.0, p99 500.0, max 500.0 ms (nearest rank)',
        '1 of them 100 ms or more',
        'all gestures: 2 steps; p50 16.0, p95 500.0, p99 500.0, max 500.0 ms (nearest rank)',
    ]:
        assert want in out, f'missing {want!r} in:\n{out}'
    assert re.search(r'over 18\.0 ms\s+100%', out), f'the only gap of the first segment is not 100%:\n{out}'


def test_same_gesture_together():
    # the same touch twice, gaps 16/20/50 ms and 16/16 ms: its second block sums both segments
    lines = ['tl: p 1 0 32768 32768 at 1000000']
    for i, done in [(21, 1016000), (22, 1032000), (23, 1052000), (24, 1102000)]:
        lines += steady(i, done)
    lines += ['tl: p 1 0 32768 32768 at 4000000']
    for i, done in [(25, 4016000), (26, 4032000), (27, 4048000)]:
        lines += steady(i, done)
    out = run(lines)
    for want in [
        'the same gestures together:',
        '2 times, 5 steps; p50 16.0, p95 50.0, p99 50.0, max 50.0 ms (nearest rank)',
        '0 of them 100 ms or more',
        'all gestures: 5 steps; p50 16.0, p95 50.0, p99 50.0, max 50.0 ms (nearest rank)',
    ]:
        assert want in out, f'missing {want!r} in:\n{out}'


def test_nothing_to_count():
    # no gesture at all, and lines no number fits into: frames.py says so and exits well
    out = run(['hello', 'tl: p x y z at w', 'not a tl line at all'])
    assert "0 gestures, 0 frames copied into the panel's memory, 0 waits" in out, out
    assert 'all gestures:' not in out, f'a run without steps printed an all-gestures line:\n{out}'


def main():
    for test in (test_long_stalls_counted, test_slow_frame_attribution, test_short_windows,
                 test_same_gesture_together, test_nothing_to_count):
        test()
        print(f'PASS: {test.__name__}')


if __name__ == '__main__':
    main()
