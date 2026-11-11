"""注册用例的验证码夹具：向服务端要一道题，顺便拿到正确答案。

不伪造 token、也不关掉校验，是为了让用例走的还是生产那条判题路径
（加密 token + 一次性 + 错误计数）；答案只存在服务端的密文里，所以只能
在这里用 ``issue_challenge()`` 拿。
"""
from app.services.captcha_service import issue_challenge


def captcha_fields() -> dict:
    challenge = issue_challenge()
    return {
        "captcha_token": challenge.token,
        "captcha_selection": sorted(challenge.answers),
    }
