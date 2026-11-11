"""全局测试夹具。

限流后端在模块导入时就构建好了，计数器跨用例共享。新增的公开接口限流
（PUBLIC_RATE_LIMIT，默认 60/分钟）会让"前面用例调用过几次产品接口"影响到
后面的用例，因此每个用例开始前清空一次，限流断言只依赖本用例内的调用。

验证码的一次性记录（用过/错几次）同理，跨用例残留会让下一题莫名"已使用"。
"""
import pytest

from app.services.captcha_service import store as captcha_store
from app.services.registration_service import rate_limiter


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    rate_limiter.clear()
    captcha_store.clear()
    yield
