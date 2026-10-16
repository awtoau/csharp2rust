"""Focused tests for C# member-name lowering."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.csharp_emitter import Emitter


class CsharpEmitterNameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.emitter = Emitter.__new__(Emitter)
        self.emitter.project = {"peripheral_methods": {"accessor_names": {
            "strip_prefixes": ["get_", "set_"],
        }}}

    def test_explicit_interface_uses_simple_member_name(self) -> None:
        self.assertEqual(
            self.emitter.fn_name(
                "Antmicro.Renode.Peripherals.SPI.ISPI_Peripheral.FinishTransmission"),
            "finish_transmission",
        )

    def test_explicit_generic_interface_uses_simple_member_name(self) -> None:
        self.assertEqual(
            self.emitter.fn_name(
                "Antmicro.Renode.Peripherals.UART.IUART<System.UInt16>.add_CharReceived"),
            "add_char_received",
        )
