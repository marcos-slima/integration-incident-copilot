#!/usr/bin/env python3
"""Testa envio de e-mail via Resend API após verificar domínio."""

import asyncio

import requests
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.admin.models import WebUser
from app.config import settings
from app.webusers import sign_email_token


def send_email_resend(to: str, subject: str, body: str) -> tuple[bool, str]:
    """Envia e-mail via API REST do Resend."""
    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {settings.smtp_password}",
        "Content-Type": "application/json",
    }
    payload = {"from": settings.smtp_from, "to": [to], "subject": subject, "text": body}

    print(f"Enviando para: {to}")
    print(f"De: {settings.smtp_from}")

    response = requests.post(url, json=payload, headers=headers, timeout=10)

    if response.status_code == 200:
        return True, "E-mail enviado com sucesso"
    else:
        return False, f"Status {response.status_code}: {response.text}"


async def main():
    print("=== Teste de envio via Resend API ===")

    async with TestingSessionLocal() as session:
        user = await session.scalar(select(WebUser).where(WebUser.username == "avaliador"))

        if user is None:
            print("Usuário 'avaliador' não encontrado. Criando...")
            from pydantic import BaseModel

            from app.admin.routes import create_user

            class UserCreate(BaseModel):
                username: str
                email: str
                phone: str
                password: str
                created_by: str

            try:
                result = await create_user(
                    UserCreate(
                        username="avaliador",
                        email=settings.smtp_from,
                        phone="+5531987554658",
                        password="minhasenha123",
                        created_by="admin-api",
                    ),
                    session,
                )
                print(f"Usuário criado: {result['username']} (ID: {result['id']})")
            except ValueError as e:
                print(f"Erro ao criar usuário: {e}")
                return

        token = sign_email_token("avaliador", 24 * 3600, settings.session_secret)
        body = f"Token de ativacao: {token}\n\nEste token expira em 24 horas."

        success, msg = await asyncio.to_thread(
            send_email_resend,
            settings.smtp_from,
            "Ativacao de conta - Integration Incident Copilot",
            body,
        )

        print(f"\nToken: {token}")
        print(f"Sucesso? {success}")

        if success:
            print("✅ E-mail real enviado! Verifique s.marcos.lima@msl.com")
        else:
            print(f"❌ Erro: {msg}")
            print("\n⚠️  Se o erro for 'domain not verified', verifique o domínio em:")
            print("     https://resend.com/domains")


engine = create_async_engine(settings.database_url, echo=False)
TestingSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

if __name__ == "__main__":
    asyncio.run(main())
