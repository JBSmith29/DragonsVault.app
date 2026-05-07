/**
 * Enhanced card hover cache with localStorage persistence and size limits
 * Improves performance by caching frequently viewed cards across sessions
 */

(function () {
  if (window.dvCardHoverCache) return;

  const CACHE_KEY = 'dv_card_hover_cache';
  const MAX_CACHE_SIZE = 500; // Maximum number of cached cards
  const CACHE_VERSION = 1;
  const CACHE_TTL = 7 * 24 * 60 * 60 * 1000; // 7 days in milliseconds

  class CardHoverCache {
    constructor() {
      this.memoryCache = new Map();
      this.loadFromStorage();
    }

    loadFromStorage() {
      try {
        const stored = localStorage.getItem(CACHE_KEY);
        if (!stored) return;

        const data = JSON.parse(stored);
        if (data.version !== CACHE_VERSION) {
          // Clear cache if version mismatch
          localStorage.removeItem(CACHE_KEY);
          return;
        }

        const now = Date.now();
        let validEntries = 0;

        // Load valid entries into memory cache
        for (const [key, entry] of Object.entries(data.entries || {})) {
          if (entry.expires > now) {
            this.memoryCache.set(key, entry);
            validEntries++;
          }
        }

        console.log(`[CardHoverCache] Loaded ${validEntries} cached cards from storage`);
      } catch (error) {
        console.warn('[CardHoverCache] Failed to load from storage:', error);
        localStorage.removeItem(CACHE_KEY);
      }
    }

    saveToStorage() {
      try {
        const entries = {};
        for (const [key, value] of this.memoryCache.entries()) {
          entries[key] = value;
        }

        const data = {
          version: CACHE_VERSION,
          entries,
          savedAt: Date.now(),
        };

        localStorage.setItem(CACHE_KEY, JSON.stringify(data));
      } catch (error) {
        console.warn('[CardHoverCache] Failed to save to storage:', error);
        // If quota exceeded, clear old entries
        if (error.name === 'QuotaExceededError') {
          this.evictOldEntries(MAX_CACHE_SIZE / 2);
          try {
            this.saveToStorage();
          } catch (retryError) {
            console.error('[CardHoverCache] Failed to save after eviction:', retryError);
          }
        }
      }
    }

    get(key) {
      const entry = this.memoryCache.get(key);
      if (!entry) return null;

      // Check if expired
      if (entry.expires < Date.now()) {
        this.memoryCache.delete(key);
        return null;
      }

      // Update access time for LRU
      entry.lastAccessed = Date.now();
      return entry.value;
    }

    set(key, value) {
      // Enforce cache size limit
      if (this.memoryCache.size >= MAX_CACHE_SIZE) {
        this.evictOldEntries(MAX_CACHE_SIZE * 0.8); // Evict 20% when full
      }

      const entry = {
        value,
        expires: Date.now() + CACHE_TTL,
        lastAccessed: Date.now(),
        createdAt: Date.now(),
      };

      this.memoryCache.set(key, entry);

      // Debounced save to storage
      this.scheduleSave();
    }

    has(key) {
      return this.memoryCache.has(key) && this.get(key) !== null;
    }

    evictOldEntries(targetSize) {
      // Sort by last accessed time (LRU)
      const entries = Array.from(this.memoryCache.entries())
        .sort((a, b) => a[1].lastAccessed - b[1].lastAccessed);

      const toRemove = entries.length - targetSize;
      for (let i = 0; i < toRemove; i++) {
        this.memoryCache.delete(entries[i][0]);
      }

      console.log(`[CardHoverCache] Evicted ${toRemove} old entries`);
    }

    scheduleSave() {
      if (this.saveTimeout) {
        clearTimeout(this.saveTimeout);
      }
      this.saveTimeout = setTimeout(() => {
        this.saveToStorage();
      }, 2000); // Save 2 seconds after last update
    }

    clear() {
      this.memoryCache.clear();
      localStorage.removeItem(CACHE_KEY);
      console.log('[CardHoverCache] Cache cleared');
    }

    getStats() {
      const now = Date.now();
      let validCount = 0;
      let expiredCount = 0;

      for (const entry of this.memoryCache.values()) {
        if (entry.expires > now) {
          validCount++;
        } else {
          expiredCount++;
        }
      }

      return {
        total: this.memoryCache.size,
        valid: validCount,
        expired: expiredCount,
        maxSize: MAX_CACHE_SIZE,
        utilizationPercent: Math.round((validCount / MAX_CACHE_SIZE) * 100),
      };
    }
  }

  // Create global cache instance
  const cache = new CardHoverCache();

  // Expose to window for use by card-hover.js
  window.dvCardHoverCache = cache;

  // Add cache stats to console (dev mode)
  if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
    console.log('[CardHoverCache] Initialized:', cache.getStats());
  }

  // Periodic cleanup of expired entries
  setInterval(() => {
    const stats = cache.getStats();
    if (stats.expired > 0) {
      cache.evictOldEntries(stats.valid);
      cache.saveToStorage();
    }
  }, 60000); // Check every minute

  // Save cache before page unload
  window.addEventListener('beforeunload', () => {
    cache.saveToStorage();
  });
})();
