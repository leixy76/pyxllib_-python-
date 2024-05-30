#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Author : 陈坤泽
# @Email  : 877362867@qq.com
# @Date   : 2024/05/29

from pyxllib.prog.pupil import check_install_package

# cachetools，通用缓存工具，适用范围更广
check_install_package('cachetools')
# cached-property，类属性特用工具，相对比较简洁些
check_install_package('cached-property')

# 对于普通函数，一般用lru_cache即可
from functools import lru_cache
import threading

from cachetools import cached, LRUCache, TTLCache

# 官方文档：https://pypi.org/project/cached-property/
from cached_property import cached_property
from cached_property import threaded_cached_property  # 线程安全
from cached_property import cached_property_with_ttl  # 限时缓存，单位秒
from cached_property import threaded_cached_property_with_ttl  # 线程 + 限时


# 进一步封装的更通用、自己常用的装饰器

def xlcache(maxsize=128, *, ttl=None, lock=None, property=False):
    def decorator(func):
        if property:
            if ttl is not None:
                if lock:
                    # 使用带有时间限制和线程安全的缓存属性
                    return threaded_cached_property_with_ttl(ttl)(func)
                else:
                    # 使用带有时间限制但非线程安全的缓存属性
                    return cached_property_with_ttl(ttl)(func)
            else:
                if lock:
                    # 使用线程安全的缓存属性
                    return threaded_cached_property(func)
                else:
                    # 使用普通的缓存属性
                    return cached_property(func)
        else:
            lock2 = threading.RLock() if lock is True else lock
            if ttl is None:
                return cached(LRUCache(maxsize), lock=lock2)(func)
            else:
                cache = TTLCache(maxsize=maxsize, ttl=ttl)
                return cached(cache, lock=lock2)(func)

    return decorator
