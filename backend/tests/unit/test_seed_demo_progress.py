"""Progress safeguards for the demo seeder."""

from __future__ import annotations

import asyncio

import app.seed_demo as seed_demo_module


async def test_seed_demo_emits_heartbeat_during_slow_work(monkeypatch, capsys) -> None:
    monkeypatch.setattr(seed_demo_module, "_SEED_DEMO_HEARTBEAT_SECONDS", 0.005)

    async def slow_seed(slug: str, yes_fake_data: bool) -> dict[str, bool]:
        await asyncio.sleep(0.03)
        return {"finished": True}

    monkeypatch.setattr(seed_demo_module, "_seed_demo", slow_seed)

    assert await seed_demo_module.seed_demo("unit-test", yes_fake_data=True) == {"finished": True}
    assert "seed_demo heartbeat: still running" in capsys.readouterr().out
