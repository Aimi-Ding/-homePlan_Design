"""手机号 + 短信验证码登录。

短信通道支持两种：
- console：验证码打到服务端日志（本地开发），SMS_DEBUG=1 时随接口返回，方便联调；
- aliyun ：阿里云短信 SendSms（RPC 签名，HMAC-SHA1）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx
from fastapi import HTTPException, Request
from sqlalchemy import select

from . import config
from .db import SessionLocal
from .models import AuthSession, LoginCode, User

PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
SEND_INTERVAL = 60          # 同一手机号两次发送的最小间隔（秒）
DAILY_LIMIT = 8             # 同一手机号每天最多发几条
MAX_ATTEMPTS = 5


def _now() -> datetime:
    """统一用 naive UTC，和 SQLite 里读出来的时间可直接比较。"""
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def normalize_phone(raw: str) -> str:
    phone = re.sub(r"[^\d]", "", raw or "")
    if not PHONE_RE.match(phone):
        raise HTTPException(400, "请输入 11 位手机号")
    return phone


SMS_ERRORS = {
    "isv.BUSINESS_LIMIT_CONTROL": "该手机号发送过于频繁，请稍后再试",
    "isv.MOBILE_NUMBER_ILLEGAL": "手机号格式不正确",
    "isv.SMS_SIGNATURE_ILLEGAL": "短信签名未审核通过或不存在，请检查签名配置",
    "isv.SMS_TEMPLATE_ILLEGAL": "短信模板未审核通过或不存在，请检查模板 CODE",
    "isv.TEMPLATE_MISSING_PARAMETERS": "短信模板缺少变量，请检查模板变量名",
    "isv.INVALID_PARAMETERS": "短信参数不合法，请检查手机号或模板配置",
    "InvalidAccessKeyId": "短信服务 AccessKey 无效，请检查配置",
    "InvalidAccessKeyId.NotFound": "短信服务 AccessKey 不存在，请检查 AccessKey ID",
    "SignatureDoesNotMatch": "短信服务签名校验失败，请检查 AccessKey Secret",
}


def _percent(value: str) -> str:
    """阿里云 RPC 签名要求的 RFC3986 编码（空格 %20，~ 不编码）。"""
    return quote(str(value), safe="~")


def _aliyun_params(action: str, extra: dict) -> dict:
    params = {
        "AccessKeyId": config.ALIYUN_ACCESS_KEY_ID,
        "Action": action,
        "Format": "JSON",
        "RegionId": config.ALIYUN_SMS_REGION,
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": uuid.uuid4().hex,
        "SignatureVersion": "1.0",
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Version": "2017-05-25",
        **extra,
    }
    canonical = "&".join(f"{_percent(k)}={_percent(params[k])}" for k in sorted(params))
    string_to_sign = f"GET&%2F&{_percent(canonical)}"
    digest = hmac.new(
        (config.ALIYUN_ACCESS_KEY_SECRET + "&").encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    params["Signature"] = base64.b64encode(digest).decode()
    return params


def _aliyun_send(phone: str, code: str) -> None:
    missing = [
        name for name, value in (
            ("ALIYUN_ACCESS_KEY_ID", config.ALIYUN_ACCESS_KEY_ID),
            ("ALIYUN_ACCESS_KEY_SECRET", config.ALIYUN_ACCESS_KEY_SECRET),
            ("ALIYUN_SMS_SIGN_NAME", config.ALIYUN_SMS_SIGN_NAME),
            ("ALIYUN_SMS_TEMPLATE_CODE", config.ALIYUN_SMS_TEMPLATE_CODE),
        ) if not value
    ]
    if missing:
        raise HTTPException(503, f"短信服务未配置完整，缺少：{'、'.join(missing)}")

    params = _aliyun_params("SendSms", {
        "PhoneNumbers": phone,
        "SignName": config.ALIYUN_SMS_SIGN_NAME,
        "TemplateCode": config.ALIYUN_SMS_TEMPLATE_CODE,
        "TemplateParam": json.dumps({config.ALIYUN_SMS_PARAM_KEY: code}),
    })
    try:
        resp = httpx.get("https://dysmsapi.aliyuncs.com/", params=params, timeout=10)
        data = resp.json()
    except Exception as exc:  # 网络 / 解析失败
        print(f"[sms] aliyun request failed: {exc}", flush=True)
        raise HTTPException(502, "短信服务连接失败，请稍后重试") from exc

    if data.get("Code") != "OK":
        name = data.get("Code") or ""
        reason = SMS_ERRORS.get(name) or f"短信发送失败（{name or '未知错误'}）"
        print(f"[sms] aliyun error {name}: {data.get('Message')}", flush=True)
        raise HTTPException(502, reason)
    # BizId 用来在控制台 / QuerySendDetails 查这条短信的实际送达状态
    print(f"[sms] aliyun accepted phone={mask_phone(phone)} bizId={data.get('BizId')} requestId={data.get('RequestId')}", flush=True)


def send_sms(phone: str, code: str) -> None:
    if config.SMS_PROVIDER == "console":
        print(f"[sms] {phone} -> {code}（console 模式，未真实发送）", flush=True)
        return
    if config.SMS_PROVIDER == "aliyun":
        _aliyun_send(phone, code)
        print(f"[sms] {mask_phone(phone)} 已提交阿里云短信", flush=True)
        return
    raise HTTPException(503, f"未知的短信通道：{config.SMS_PROVIDER}")


def issue_code(raw_phone: str) -> tuple[str, str]:
    phone = normalize_phone(raw_phone)
    now = _now()
    with SessionLocal() as s:
        last = s.scalars(select(LoginCode).where(LoginCode.phone == phone)
                         .order_by(LoginCode.id.desc()).limit(1)).first()
        if last and (now - last.created_at).total_seconds() < SEND_INTERVAL:
            wait = SEND_INTERVAL - int((now - last.created_at).total_seconds())
            raise HTTPException(429, f"发送太频繁，请 {wait} 秒后再试")
        today = s.scalars(select(LoginCode).where(LoginCode.phone == phone,
                                                  LoginCode.created_at > now - timedelta(days=1))).all()
        if len(today) >= DAILY_LIMIT:
            raise HTTPException(429, "今天验证码次数已用完，请明天再试")

        code = f"{secrets.randbelow(1_000_000):06d}"
        row = LoginCode(phone=phone, code=code, expires_at=now + timedelta(seconds=config.CODE_TTL_SECONDS))
        s.add(row)
        s.commit()
        row_id = row.id

    try:
        send_sms(phone, code)
    except Exception:
        # 发送失败要删掉占位记录，否则这次失败会把用户锁在 60 秒冷却里
        with SessionLocal() as s2:
            failed = s2.get(LoginCode, row_id)
            if failed:
                s2.delete(failed)
                s2.commit()
        raise
    return phone, code


def verify_code(raw_phone: str, code: str) -> tuple[User, str]:
    phone = normalize_phone(raw_phone)
    code = re.sub(r"\D", "", code or "")
    if len(code) != 6:
        raise HTTPException(400, "请输入 6 位验证码")
    now = _now()
    with SessionLocal() as s:
        row = s.scalars(select(LoginCode).where(LoginCode.phone == phone, LoginCode.used == 0)
                        .order_by(LoginCode.id.desc()).limit(1)).first()
        if not row:
            raise HTTPException(400, "请先获取验证码")
        if row.expires_at < now:
            raise HTTPException(400, "验证码已过期，请重新获取")
        if row.attempts >= MAX_ATTEMPTS:
            row.used = 1
            s.commit()
            raise HTTPException(400, "验证码错误次数过多，请重新获取")
        if not hmac.compare_digest(row.code, code):
            row.attempts += 1
            s.commit()
            raise HTTPException(400, "验证码不正确")

        row.used = 1
        user = s.scalars(select(User).where(User.phone == phone)).first()
        if not user:
            user = User(phone=phone)
            s.add(user)
            s.flush()
        user.last_login = now

        token = secrets.token_urlsafe(32)
        s.add(AuthSession(token=token, user_id=user.id, expires_at=now + timedelta(days=config.SESSION_DAYS)))
        # 顺手清理过期会话
        for old in s.scalars(select(AuthSession).where(AuthSession.expires_at < now)).all():
            s.delete(old)
        s.commit()
        s.refresh(user)
        return user, token


def current_user(request: Request) -> User:
    token = request.cookies.get("sid")
    if not token:
        raise HTTPException(401, "请先登录")
    with SessionLocal() as s:
        sess = s.get(AuthSession, token)
        if not sess or sess.expires_at < _now():
            raise HTTPException(401, "登录已过期，请重新登录")
        user = s.get(User, sess.user_id)
        if not user:
            raise HTTPException(401, "账号不存在")
        return user


def logout(token: str | None) -> None:
    if not token:
        return
    with SessionLocal() as s:
        sess = s.get(AuthSession, token)
        if sess:
            s.delete(sess)
            s.commit()


def mask_phone(phone: str) -> str:
    return f"{phone[:3]}****{phone[-4:]}" if len(phone) == 11 else phone


def user_out(user: User) -> dict:
    return {"id": user.id, "phone": mask_phone(user.phone), "nickname": user.nickname or mask_phone(user.phone)}
