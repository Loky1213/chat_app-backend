from django.core.cache import cache

class CacheService:
    @staticmethod
    def get(key):
        return cache.get(key)

    @staticmethod
    def set(key, value, timeout=300):
        cache.set(key, value, timeout)

    @staticmethod
    def delete(key):
        cache.delete(key)
