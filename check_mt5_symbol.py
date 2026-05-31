"""
ตรวจสอบ symbol ที่ใช้ได้ในบัญชี XM และข้อมูลสำคัญ
รันก่อนเริ่ม forward test เพื่อยืนยันว่าใช้ symbol ถูก
"""
import MetaTrader5 as mt5
import os
from dotenv import load_dotenv

load_dotenv()

# Connect to MT5
if not mt5.initialize(
    login=int(os.getenv("MT5_LOGIN")),
    password=os.getenv("MT5_PASSWORD"),
    server=os.getenv("MT5_SERVER"),
):
    print(f"MT5 init failed: {mt5.last_error()}")
    exit()

print(f"Connected to: {mt5.account_info().server}")
print(f"Account: {mt5.account_info().login}")
print(f"Balance: ${mt5.account_info().balance}")
print()

# Find all gold-related symbols
print("=" * 60)
print("ALL GOLD-RELATED SYMBOLS IN YOUR ACCOUNT:")
print("=" * 60)

all_symbols = mt5.symbols_get()
gold_symbols = [s for s in all_symbols if 
                "GOLD" in s.name.upper() or 
                "XAU" in s.name.upper()]

for sym in gold_symbols:
    info = mt5.symbol_info(sym.name)
    if info is None:
        continue
    
    # ต้อง select symbol ก่อนถึงจะดึงราคาได้
    mt5.symbol_select(sym.name, True)
    tick = mt5.symbol_info_tick(sym.name)
    
    print(f"\nSymbol: {sym.name}")
    print(f"  Description:    {info.description}")
    print(f"  Digits:         {info.digits}")
    print(f"  Contract size:  {info.trade_contract_size} oz/lot")
    print(f"  Min lot:        {info.volume_min}")
    print(f"  Max lot:        {info.volume_max}")
    print(f"  Lot step:       {info.volume_step}")
    print(f"  Spread:         {info.spread} points")
    if tick:
        print(f"  Bid:            {tick.bid}")
        print(f"  Ask:            {tick.ask}")
        print(f"  Spread $:       ${tick.ask - tick.bid:.2f}")
    print(f"  Trade mode:     {info.trade_mode}")  
    # 0=disabled, 1=long only, 2=short only, 3=close only, 4=full

print("\n" + "=" * 60)
print("RECOMMENDATION:")
print("=" * 60)
print("Use the symbol with:")
print("  - trade_mode = 4 (full trading)")
print("  - contract_size = 100 (standard XAUUSD)")
print("  - matches your account type (Standard/Zero/Micro)")
print()
print("Update SYMBOL constant in gold_trading_agents.py to match.")

mt5.shutdown()