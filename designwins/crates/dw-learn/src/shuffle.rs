//! `CPython`'s MT19937 + `random.shuffle`, replayed byte-for-byte.
//!
//! The v1/v2 train/holdout split seeds `random.Random(1788172800)` and
//! shuffles the pairs; reproducing the split byte-compat (contract C8)
//! therefore means reproducing `CPython`'s generator exactly — the seed
//! init (`init_by_array`), the 32-bit tempering, `getrandbits`, and the
//! `_randbelow` rejection loop that `shuffle` drives.

const N: usize = 624;
const M: usize = 397;
const MATRIX_A: u32 = 0x9908_b0df;
const UPPER_MASK: u32 = 0x8000_0000;
const LOWER_MASK: u32 = 0x7fff_ffff;

/// A CPython-compatible MT19937 instance.
pub struct Mt19937 {
    mt: [u32; N],
    mti: usize,
}

impl Mt19937 {
    /// `random.Random(seed)` for a non-negative integer seed.
    #[must_use]
    pub fn seeded(seed: u64) -> Self {
        let mut state = Self {
            mt: [0; N],
            mti: N + 1,
        };
        // `random_seed`: the integer's absolute value split into little-endian
        // 32-bit key words (at least one word).
        let mut key: Vec<u32> = Vec::new();
        let mut n = seed;
        loop {
            key.push((n & 0xffff_ffff) as u32);
            n >>= 32;
            if n == 0 {
                break;
            }
        }
        state.init_by_array(&key);
        state
    }

    // A faithful port of CPython's `_randommodule.c`/MT19937 reference: every
    // index is bounded by the compile-time `N`/`M` constants, so the indexing
    // and `as u32` casts below cannot panic or truncate (the loop counters are
    // < N = 624).
    #[allow(
        clippy::indexing_slicing,
        clippy::cast_possible_truncation,
        reason = "MT19937 reference loop, indices bounded by N = 624"
    )]
    fn init_genrand(&mut self, seed: u32) {
        self.mt[0] = seed;
        for i in 1..N {
            self.mt[i] = (1_812_433_253u32)
                .wrapping_mul(self.mt[i - 1] ^ (self.mt[i - 1] >> 30))
                .wrapping_add(i as u32);
        }
        self.mti = N;
    }

    #[allow(
        clippy::indexing_slicing,
        clippy::cast_possible_truncation,
        reason = "MT19937 reference init, indices bounded by N = 624"
    )]
    fn init_by_array(&mut self, key: &[u32]) {
        self.init_genrand(19_650_218);
        let mut i = 1usize;
        let mut j = 0usize;
        let mut k = N.max(key.len());
        while k > 0 {
            self.mt[i] = (self.mt[i]
                ^ ((self.mt[i - 1] ^ (self.mt[i - 1] >> 30)).wrapping_mul(1_664_525)))
            .wrapping_add(key[j])
            .wrapping_add(j as u32);
            i += 1;
            j += 1;
            if i >= N {
                self.mt[0] = self.mt[N - 1];
                i = 1;
            }
            if j >= key.len() {
                j = 0;
            }
            k -= 1;
        }
        let mut k = N - 1;
        while k > 0 {
            self.mt[i] = (self.mt[i]
                ^ ((self.mt[i - 1] ^ (self.mt[i - 1] >> 30)).wrapping_mul(1_566_083_941)))
            .wrapping_sub(i as u32);
            i += 1;
            if i >= N {
                self.mt[0] = self.mt[N - 1];
                i = 1;
            }
            k -= 1;
        }
        self.mt[0] = 0x8000_0000;
    }

    #[allow(
        clippy::indexing_slicing,
        reason = "MT19937 reference twist, indices bounded by N = 624"
    )]
    fn genrand_uint32(&mut self) -> u32 {
        if self.mti >= N {
            if self.mti == N + 1 {
                self.init_genrand(54_823_040);
            }
            for kk in 0..(N - M) {
                let y = (self.mt[kk] & UPPER_MASK) | (self.mt[kk + 1] & LOWER_MASK);
                self.mt[kk] = self.mt[kk + M] ^ (y >> 1) ^ if y & 1 == 1 { MATRIX_A } else { 0 };
            }
            for kk in (N - M)..(N - 1) {
                let y = (self.mt[kk] & UPPER_MASK) | (self.mt[kk + 1] & LOWER_MASK);
                self.mt[kk] =
                    self.mt[kk + M - N] ^ (y >> 1) ^ if y & 1 == 1 { MATRIX_A } else { 0 };
            }
            let y = (self.mt[N - 1] & UPPER_MASK) | (self.mt[0] & LOWER_MASK);
            self.mt[N - 1] = self.mt[M - 1] ^ (y >> 1) ^ if y & 1 == 1 { MATRIX_A } else { 0 };
            self.mti = 0;
        }
        let mut y = self.mt[self.mti];
        self.mti += 1;
        y ^= y >> 11;
        y ^= (y << 7) & 0x9d2c_5680;
        y ^= (y << 15) & 0xefc6_0000;
        y ^= y >> 18;
        y
    }

    /// `CPython` `getrandbits(k)` for `k` in 1..=32.
    fn getrandbits32(&mut self, k: u32) -> u32 {
        self.genrand_uint32() >> (32 - k)
    }

    /// `CPython` `_randbelow_with_getrandbits(n)` for `n` in 1..=2^32.
    fn randbelow(&mut self, n: u32) -> u32 {
        let k = 32 - n.leading_zeros();
        loop {
            let r = self.getrandbits32(k);
            if r < n {
                return r;
            }
        }
    }

    /// `CPython` `random.shuffle`: Fisher-Yates from the top down.
    // `i + 1 <= len`, and `len` is a slice length (fits u32 for any real pair
    // set); the cast and truncation warnings do not apply in practice.
    #[allow(
        clippy::cast_possible_truncation,
        reason = "slice length fits u32 for any realistic pair set"
    )]
    pub fn shuffle<T>(&mut self, values: &mut [T]) {
        if values.len() <= 1 {
            return;
        }
        for i in (1..values.len()).rev() {
            let j = self.randbelow((i + 1) as u32) as usize;
            values.swap(i, j);
        }
    }
}

/// `random.Random(seed).shuffle(values)` — the split's source of truth.
#[must_use]
pub fn seeded_shuffle<T: Clone>(seed: u64, values: &[T]) -> Vec<T> {
    let mut out = values.to_vec();
    Mt19937::seeded(seed).shuffle(&mut out);
    out
}
