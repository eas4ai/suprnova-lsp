//! Optional execution-time samples for the calling thread.

/// A thread CPU clock sample in microseconds.
///
/// Capture and finish on the same thread. This clock excludes time spent waiting and work done by
/// other threads; it does not explain why a wall-clock interval is longer. Unsupported platforms and
/// failed clock reads return `None` instead of manufacturing an execution-time value.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ThreadCpuTime(u64);

impl ThreadCpuTime {
    /// Sample only when the caller's diagnostic is enabled.
    pub fn capture(enabled: bool) -> Option<Self> {
        if !enabled {
            return None;
        }
        #[cfg(target_os = "linux")]
        {
            let mut time = libc::timespec {
                tv_sec: 0,
                tv_nsec: 0,
            };
            // SAFETY: clock_gettime writes one initialized timespec through this valid pointer.
            let result = unsafe { libc::clock_gettime(libc::CLOCK_THREAD_CPUTIME_ID, &mut time) };
            Self::from_clock_read(result, i128::from(time.tv_sec), i128::from(time.tv_nsec))
        }
        #[cfg(not(target_os = "linux"))]
        {
            None
        }
    }

    /// Read this thread's clock again and return a checked difference from the sample.
    pub fn elapsed_us(self) -> Option<u64> {
        self.elapsed_at(Self::capture(true))
    }

    // Keep conversion and subtraction separate from the OS call so failed reads and arithmetic
    // boundaries can be checked deterministically without depending on machine timing.
    #[cfg(any(target_os = "linux", test))]
    fn from_clock_read(result: i32, seconds: i128, nanoseconds: i128) -> Option<Self> {
        if result != 0 {
            return None;
        }
        let seconds = u64::try_from(seconds).ok()?;
        let nanoseconds = u64::try_from(nanoseconds).ok()?;
        seconds
            .checked_mul(1_000_000)?
            .checked_add(nanoseconds / 1_000)
            .map(Self)
    }

    fn elapsed_at(self, finished: Option<Self>) -> Option<u64> {
        finished?.0.checked_sub(self.0)
    }
}

#[cfg(test)]
mod tests {
    use super::ThreadCpuTime;

    #[test]
    fn disabled_capture_is_absent() {
        assert_eq!(ThreadCpuTime::capture(false), None);
    }

    #[cfg(not(target_os = "linux"))]
    #[test]
    fn unsupported_capture_and_elapsed_are_absent() {
        assert_eq!(ThreadCpuTime::capture(true), None);
        assert_eq!(ThreadCpuTime(0).elapsed_us(), None);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn enabled_capture_can_finish_on_the_same_thread() {
        let started = ThreadCpuTime::capture(true).expect("Linux thread CPU clock is available");
        assert!(started.elapsed_us().is_some());
    }

    #[test]
    fn failed_clock_read_is_absent() {
        assert_eq!(ThreadCpuTime::from_clock_read(-1, 0, 0), None);
    }

    #[test]
    fn signed_and_oversized_clock_parts_are_rejected() {
        assert_eq!(ThreadCpuTime::from_clock_read(0, -1, 0), None);
        assert_eq!(ThreadCpuTime::from_clock_read(0, 0, -1), None);
        assert_eq!(ThreadCpuTime::from_clock_read(0, i128::MAX, 0), None);
        assert_eq!(ThreadCpuTime::from_clock_read(0, 0, i128::MAX), None);
    }

    #[test]
    fn clock_parts_preserve_microsecond_precision() {
        assert_eq!(
            ThreadCpuTime::from_clock_read(0, 2, 123_456_999),
            Some(ThreadCpuTime(2_123_456)),
        );
    }

    #[test]
    fn clock_conversion_rejects_multiplication_and_addition_overflow() {
        let max_seconds = u64::MAX / 1_000_000;
        assert_eq!(
            ThreadCpuTime::from_clock_read(0, i128::from(max_seconds) + 1, 0),
            None,
        );
        let remaining_us = u64::MAX % 1_000_000;
        assert_eq!(
            ThreadCpuTime::from_clock_read(
                0,
                i128::from(max_seconds),
                i128::from(remaining_us + 1) * 1_000,
            ),
            None,
        );
    }

    #[test]
    fn elapsed_rejects_failed_read_and_clock_regression() {
        let started = ThreadCpuTime(20);
        assert_eq!(started.elapsed_at(None), None);
        assert_eq!(started.elapsed_at(Some(ThreadCpuTime(19))), None);
        assert_eq!(started.elapsed_at(Some(ThreadCpuTime(20))), Some(0));
        assert_eq!(started.elapsed_at(Some(ThreadCpuTime(27))), Some(7));
    }
}
