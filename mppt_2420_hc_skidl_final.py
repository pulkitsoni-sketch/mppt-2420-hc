"""
mppt_2420_hc_skidl.py  —  Libre Solar MPPT 2420 HC (Rev 0.2.3, 2021-01-06)
===========================================================================
SKiDL script to regenerate the KiCad netlist and grouped BOM for the
MPPT 2420 HC charge controller with high-side load switch and CAN bus.

Hardware overview:
  MPPT input  : 60 V, 10 A (solar panel)
  Battery     : 12 V / 24 V, 20 A
  Load output : 20 A (high-side MOSFET switch)
  Interface   : CAN bus (TCAN334, RJ45 daisy-chain)

Key ICs:
  U1  LM5107          Half-bridge MOSFET gate driver
  U2  STM32G431CBTx   ARM Cortex-M4 MCU (170 MHz, LQFP-48)
  U3  INA186          DC/DC inductor current-sense amplifier
  U4  LMR16006X       4–60 V → 3.3 V step-down SMPS regulator
  U5  INA186          Load current-sense amplifier
  U6  W25Q80DVS       8 Mbit SPI flash memory
  U7  TCAN334         1 Mbps CAN bus transceiver

Schematic source : mppt-2420-hc_schematic.pdf  (KiCad 5.1.9, 6 sheets)
BOM source       : mppt-2420-hc_bom__hv_supply_can_.csv

IMPORTANT — explicit reference designators
-----------------------------------------
Every instantiated part has its .ref set explicitly so that the generated
BOM reference strings match the original schematic exactly. Without this,
SKiDL's sequential auto-numbering re-orders references whenever the
instantiation order changes, producing value/reference mismatches.

Schematic sheets covered:
  Sheet 1 – Top level  (connectors, fuse, mounting)
  Sheet 2 – DC/DC      (half-bridge, inductor, current/voltage sense)
  Sheet 3 – Power supply (supply selection, SMPS 3.3 V, charge pump, +12 V)
  Sheet 4 – MCU        (STM32G431, SPI flash, LEDs, UEXT, SWD)
  Sheet 5 – Load switch (high-side MOSFET, current sense, comparator)
  Sheet 6 – CAN        (TCAN334, termination, RJ45 power, polyfuses)

Usage:
    python mppt_2420_hc_skidl.py
Outputs:
    mppt_2420_hc_skidl.net  — KiCad-compatible netlist
    mppt_2420_hc_BOM.csv    — grouped Bill of Materials
"""

import os
import csv
from collections import defaultdict


# =============================================================================
# 1.  SETUP & PATHS  (edit to match your KiCad installation)
# =============================================================================
app_symbols    = '/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols'
app_footprints = '/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints'
user_config    = '/Users/user/Documents/KiCad/9.0'  # adjust as needed

os.environ['KICAD_SYMBOL_DIR']  = app_symbols
os.environ['KICAD6_SYMBOL_DIR'] = app_symbols
os.environ['KICAD7_SYMBOL_DIR'] = app_symbols
os.environ['KICAD8_SYMBOL_DIR'] = app_symbols
os.environ['KICAD9_SYMBOL_DIR'] = app_symbols

os.environ['KICAD_FOOTPRINT_DIR']  = app_footprints
os.environ['KICAD8_FOOTPRINT_DIR'] = app_footprints

from skidl import *  # noqa: E402  (must follow env-var setup)

lib_search_paths[KICAD].extend([app_symbols, user_config])
footprint_search_paths[KICAD].append(app_footprints)
set_default_tool(KICAD)


# =============================================================================
# 2.  GLOBAL / POWER RAIL NETS
# =============================================================================
gnd        = Net('GND')
v3v3       = Net('+3.3V')       # Regulated 3.3 V output of U4 (LMR16006X SMPS)
v12        = Net('+12V')        # 12 V MOSFET driver supply (emitter-follower T10)
vdda       = Net('VDDA')        # MCU analogue supply (filtered from +3.3V via R40)
vref_p     = Net('VREF+')       # MCU external voltage reference (filtered via R27)

# Main power rails on the board
dcdc_hv_p  = Net('DCDC_HV+')   # Solar HV input positive (up to 60 V)
dcdc_hv_n  = Net('DCDC_HV-')   # Solar HV input negative (via reverse-polarity Q3)
dcdc_lv_p  = Net('DCDC_LV+')   # Battery / LV output rail (DC/DC converter output)
bat_p      = Net('BAT+')        # Battery terminal positive (after fuse XF1 / F1)
load_p     = Net('LOAD+')       # Load output positive (after high-side switch Q7)
supply_in  = Net('SUPPLY_INPUT')# Internal supply rail (HV or LV selected by T2/D10)


# =============================================================================
# 3.  SIGNAL NETS — all names match the KiCad .sch net labels
# =============================================================================

# --- DC/DC power stage internal nodes (Sheet 2) ---
sw_node      = Net('SW_NODE')       # Buck switching node  (Q1 source / Q2 drain)
hs_drv       = Net('HS_DRV')        # High-side gate drive output of LM5107
ls_drv       = Net('LS_DRV')        # Low-side gate drive output of LM5107
hb_node      = Net('HB')            # Bootstrap capacitor high-voltage node
shunt_dcdc_p = Net('SHUNT_DCDC_P')  # DC/DC shunt resistor, positive (Q2 source)
shunt_dcdc_n = Net('SHUNT_DCDC_N')  # DC/DC shunt resistor, negative (→ GND)

# --- DC/DC measurement → MCU ADC/DAC (Sheet 2) ---
v_dcdc_h     = Net('V_DCDC_H')     # HV-side voltage divider output → ADC1_IN15
v_dcdc_l     = Net('V_DCDC_L')     # LV-side voltage divider output → ADC1_IN12
i_dcdc       = Net('I_DCDC')       # INA186 U3 output (inductor current) → ADC12_IN1
i_dcdc_ref   = Net('I_DCDC_REF')   # DAC1 reference input to INA186 U3

# --- PWM drive from MCU to gate driver (Sheets 2 & 4) ---
pwm_hs       = Net('PWM_HS')        # TIM1_CH1  → LM5107 HI → Q1 high-side gate
pwm_ls       = Net('PWM_LS')        # TIM1_CH1N → LM5107 LI → Q2 low-side gate

# --- Power supply internal nodes (Sheet 3) ---
cp_out       = Net('CP_OUT')        # Charge pump output (boosted above DCDC_LV+)
cp_sw        = Net('CP_SW')         # Charge pump AC switching node (T3/T4)
cp_pwm       = Net('CP_PWM')        # Charge pump PWM drive: TIM8_CH2 → T1 base

# --- Load switch nets (Sheet 5) ---
meas_lv_p    = Net('MEAS_LV+')     # Q7 gate drive node (CP_OUT via R58)
load_dis     = Net('LOAD_DIS')      # GPIOB_2 → T5 base (disable load switch)
adc12_in2  = Net('ADC12_IN2')
i_load       = Net('I_LOAD')        # INA186 U5 output (load current) → ADC12_IN2
i_load_comp  = Net('I_LOAD_COMP')   # Load over-current signal → MCU COMP2_INP
shunt_load_p = Net('SHUNT_LOAD_P')  # Load shunt positive (Q7 source)
shunt_load_n = Net('SHUNT_LOAD_N')  # Load shunt negative (→ DCDC_LV+)

# --- CAN bus (Sheet 6) ---
can_h        = Net('CAN_H')
can_l        = Net('CAN_L')
can_tx       = Net('CAN_TX')        # MCU FDCAN1_TX → U7 TXD
can_rx       = Net('CAN_RX')        # MCU FDCAN1_RX ← U7 RXD
can_stb      = Net('CAN_STB')       # GPIOB_11 → U7 STB (standby control)
can_pwr1     = Net('CAN_PWR1')      # CAN bus power supply rail 1 (J4/J5 pin 4)
can_pwr2     = Net('CAN_PWR2')      # CAN bus power supply rail 2 (J4/J5 pin 5)
can_gnd      = Net('CAN_GND')       # CAN bus ground (isolated via D6)
vbus         = Net('VBUS')           # CAN bus PoE-style power input (RJ45)

# --- MCU pin-function nets (Sheet 4) ---
osc_in       = Net('OSC_IN')        # Crystal Y1 → PF0
osc_out      = Net('OSC_OUT')       # Crystal Y1 ← PF1
spi2_cs      = Net('SPI2_CS')       # SPI flash chip-select → U6 pin 1
spi2_sck     = Net('SPI2_SCK')      # SPI clock → U6 pin 6
spi2_mosi    = Net('SPI2_MOSI')     # SPI MOSI  → U6 pin 5
spi2_miso    = Net('SPI2_MISO')     # SPI MISO  ← U6 pin 2
usart1_tx    = Net('USART1_TX')     # USART / UEXT TX
usart1_rx    = Net('USART1_RX')     # USART / UEXT RX
i2c1_scl     = Net('I2C1_SCL')      # I2C clock (UEXT)
i2c1_sda     = Net('I2C1_SDA')      # I2C data  (UEXT)
swdio        = Net('SWDIO')          # SWD data
swclk        = Net('SWCLK')          # SWD clock
nrst         = Net('NRST')           # MCU reset

# --- Misc internal nets ---
pwr_info     = Net('PWR_INFO')       # Peripheral power monitor (GPIOB_10 / J6)
net_rt1      = Net('Net-(RT1-Pad2)') # NTC thermistor divider mid-point
net_t1_b     = Net('Net-(T1-Base)')  # CP PWM NPN T1 base drive
net_t1_c     = Net('Net-(T1-C)')     # T1 collector / T4 base
net_t2_b     = Net('Net-(T2-Base)')  # Supply-select NPN T2 base drive
net_t3_b     = Net('Net-(T3-Base)')  # Charge-pump NPN T3 base drive
net_t5_b     = Net('Net-(T5-Base)')  # Load-disable NPN T5 base drive
net_t10_b    = Net('Net-(T10-Base)') # +12 V emitter-follower NPN T10 base
net_fb_u4    = Net('Net-(U4-FB)')    # LMR16006X feedback divider mid-point
net_sw_u4    = Net('Net-(U4-SW)')    # LMR16006X switch node (→ L2 → L3 → 3.3V)
net_u4_cb    = Net('Net-(U4-CB)')    # LMR16006X bootstrap cap node
net_led_g1   = Net('Net-(LED1-A1)')  # LED1 green channel anode
net_led_r1   = Net('Net-(LED1-A2)')  # LED1 red channel anode
net_led_g2   = Net('Net-(LED2-A1)')  # LED2 green channel anode
net_led_r2   = Net('Net-(LED2-A2)')  # LED2 red channel anode


# =============================================================================
# 4.  FOOTPRINT CONSTANTS & COMPONENT FACTORIES
# =============================================================================
FP_C0603  = 'Capacitor_SMD:C_0603_1608Metric'
FP_C0805  = 'Capacitor_SMD:C_0805_2012Metric'
FP_C1210  = 'Capacitor_SMD:C_1210_3225Metric'
FP_CP_D10 = 'Capacitor_THT:CP_Radial_D10.0mm_P5.00mm'
FP_CP_D18 = 'Capacitor_THT:CP_Radial_D18.0mm_P7.50mm'
FP_C_FILM = 'Capacitor_THT:C_Rect_L7.2mm_W3.5mm_P5.00mm_FKS2_FKP2_MKS2_MKP2'
FP_R0603  = 'Resistor_SMD:R_0603_1608Metric'
FP_R0805  = 'Resistor_SMD:R_0805_2012Metric'
FP_R1206  = 'Resistor_SMD:R_1206_3216Metric'
FP_R2512  = 'Resistor_SMD:R_2512_6332Metric'          # Shunt (Bourns CRE2512)
FP_SOT23  = 'Package_TO_SOT_SMD:SOT-23'
FP_SOT363 = 'Package_TO_SOT_SMD:SOT-363_SC-70-6'
FP_TO220H = 'Package_TO_SOT_THT:TO-220-3_Horizontal_BottomHeatsink'
FP_SOT223 = 'Package_TO_SOT_SMD:SOT-223-3_TabPin2'
FP_SOIC8  = 'Package_SO:SOIC-8_3.9x4.9mm_P1.27mm'
FP_LQFP48 = 'Package_QFP:LQFP-48_7x7mm_P0.5mm'
FP_SOT23_6 = 'Package_TO_SOT_SMD:SOT-23-6'


def make_cap(ref, value, net_pos, net_neg=None, footprint=FP_C0603):
    """Instantiate a capacitor, connect pin 1 to net_pos and pin 2 to net_neg
    (defaults to GND), and assign an explicit reference designator."""
    c = Part('Device', 'C', value=value, footprint=footprint)
    c.ref = ref
    c[1] += net_pos
    c[2] += (net_neg if net_neg is not None else gnd)
    return c


def make_res(ref, value, net_1, net_2, footprint=FP_R0603):
    """Instantiate a resistor and assign an explicit reference designator."""
    r = Part('Device', 'R', value=value, footprint=footprint)
    r.ref = ref
    r[1] += net_1
    r[2] += net_2
    return r


# =============================================================================
# 5.  DC/DC POWER STAGE  (Sheet 2: dcdc.sch)
#     Buck converter: HV solar input → LV battery/load output
#     Q1 (HS) + Q2 (LS) half-bridge, L1 inductor, driven by LM5107 U1
# =============================================================================

# --- Q3: Reverse-polarity protection & PV reverse-current blocking ---
# Source follower topology: gate+source tied → body diode blocks reverse panel voltage
q_nmos_t = Part('Device', 'R', dest=TEMPLATE)
q_nmos_t.name, q_nmos_t.ref_prefix = 'IPA045N10N3G', 'Q'
q_nmos_t.footprint = FP_TO220H
q_nmos_t.pins = [
    Pin(num='1', name='G'),
    Pin(num='2', name='S'),
    Pin(num='3', name='D'),
]

q3 = q_nmos_t()
q3.ref   = 'Q3'
q3.value = 'IPA045N10N3G'
q3['D'] += dcdc_hv_n     # Drain → panel negative terminal
q3['G'] += gnd            # Gate pulled low (self-protecting topology)
q3['S'] += gnd            # Source → board GND

# --- Q1: High-side buck MOSFET ---
q1 = q_nmos_t()
q1.ref   = 'Q1'
q1.value = 'IPA045N10N3G'
q1['D'] += dcdc_hv_p     # Drain → HV bus
q1['G'] += hs_drv         # Gate ← LM5107 HO
q1['S'] += sw_node        # Source → switching node

# --- Q2: Low-side synchronous-rectifier MOSFET ---
q2 = q_nmos_t()
q2.ref   = 'Q2'
q2.value = 'IPA045N10N3G'
q2['D'] += sw_node        # Drain → switching node
q2['G'] += ls_drv         # Gate ← LM5107 LO
q2['S'] += shunt_dcdc_p   # Source → current shunt positive

# --- Gate resistors: 3R3 in series with each gate (switching speed / EMI tuning) ---
# R1, R2 on high-side; R3, R5 on low-side paths (all 0805 per BOM)
make_res('R1', '3R3', hs_drv,  q1['G'], footprint=FP_R0805)
make_res('R2', '3R3', ls_drv,  q2['G'], footprint=FP_R0805)
make_res('R3', '3R3', hs_drv,  sw_node, footprint=FP_R0805)   # gate clamp / Vgs bleed
make_res('R5', '3R3', ls_drv,  gnd,     footprint=FP_R0805)   # gate clamp / Vgs bleed

# --- Gate damping resistors 4R7 in driver output stage (1206 for power rating) ---
make_res('R4',  '4R7', v12,   hs_drv, footprint=FP_R1206)
make_res('R12', '4R7', gnd,   ls_drv, footprint=FP_R1206)

# --- U1: LM5107 half-bridge gate driver ---
u1_t = Part('Device', 'R', dest=TEMPLATE)
u1_t.name, u1_t.ref_prefix = 'LM5107', 'U'
u1_t.footprint = FP_SOIC8
u1_t.pins = [
    Pin(num='1', name='VDD'),   # Driver supply (+12 V)
    Pin(num='2', name='HI'),    # High-side PWM input
    Pin(num='3', name='LI'),    # Low-side  PWM input
    Pin(num='4', name='VSS'),   # Ground
    Pin(num='5', name='LO'),    # Low-side gate drive output
    Pin(num='6', name='HS'),    # High-side source (switching node reference)
    Pin(num='7', name='HO'),    # High-side gate drive output
    Pin(num='8', name='HB'),    # High-side bootstrap rail
]
u1 = u1_t()
u1.ref   = 'U1'
u1.value = 'LM5107'
u1['VDD'] += v12
u1['HI']  += pwm_hs
u1['LI']  += pwm_ls
u1['VSS'] += gnd
u1['LO']  += ls_drv
u1['HS']  += sw_node
u1['HO']  += hs_drv
u1['HB']  += hb_node

# --- LM5107 supply decoupling ---
make_cap('C10', '100nF', v12)              # VDD bypass (100nF, 0603)
make_cap('C12', '2.2µF', v12)             # VDD bulk  (2.2µF, 0603)

# --- D16: Catch diode protecting +12V rail (1N4148 SOD-123) ---
d_1n4148_t = Part('Device', 'R', dest=TEMPLATE)
d_1n4148_t.name, d_1n4148_t.ref_prefix = '1N4148W-7-F', 'D'
d_1n4148_t.footprint = 'Diode_SMD:D_SOD-123'
d_1n4148_t.pins = [Pin(num='1', name='A'), Pin(num='2', name='K')]

d16 = d_1n4148_t()
d16.ref   = 'D16'
d16.value = '1N4148'
d16['A'] += gnd
d16['K'] += v12

# --- D14: Bootstrap diode (SS14FL) — charges C42 bootstrap cap ---
d_ss14_t = Part('Device', 'R', dest=TEMPLATE)
d_ss14_t.name, d_ss14_t.ref_prefix = 'SS14FL', 'D'
d_ss14_t.footprint = 'Diode_SMD:D_SOD-123'
d_ss14_t.pins = [Pin(num='1', name='A'), Pin(num='2', name='K')]

d14 = d_ss14_t()
d14.ref   = 'D14'
d14.value = 'SS14FL'
d14['A'] += v12
d14['K'] += hb_node

# --- D15: Free-wheeling diode across low-side MOSFET Q2 (SS14FL) ---
d15 = d_ss14_t()
d15.ref   = 'D15'
d15.value = 'SS14FL'
d15['A'] += gnd
d15['K'] += sw_node

# --- D7: Input blocking diode on HV path (SS14FL) ---
d7 = d_ss14_t()
d7.ref   = 'D7'
d7.value = 'SS14FL'
d7['A'] += dcdc_hv_n
d7['K'] += gnd

# --- C42, C44: Bootstrap / gate-drive film capacitors (0.22µF, 100V) ---
film_t = Part('Device', 'R', dest=TEMPLATE)
film_t.name, film_t.ref_prefix = 'B32529C224J', 'C'
film_t.footprint = FP_C_FILM
film_t.pins = [Pin(num='1', name='+'), Pin(num='2', name='-')]

c42 = film_t()
c42.ref   = 'C42'
c42.value = '0.22µF'
c42['+'] += hb_node    # Bootstrap cap: HB node to SW_NODE
c42['-'] += sw_node

c44 = film_t()
c44.ref   = 'C44'
c44.value = '0.22µF'
c44['+'] += sw_node    # Snubber / gate drive reservoir across low-side
c44['-'] += gnd

# --- L1: Main power inductor 53µH (custom Sendust toroid, 2×MS-130060-2) ---
# Winding: 21 turns, 40 strands × AWG27 (4 mm² total), AL = 61 nH/T²
l1_t = Part('Device', 'R', dest=TEMPLATE)
l1_t.name, l1_t.ref_prefix = 'Custom_Sendust_Toroid', 'L'
l1_t.footprint = 'Inductor_THT:Inductor_Toroid_D32.8mm_4mm2'
l1_t.pins = [Pin(num='1', name='~'), Pin(num='2', name='~')]

l1 = l1_t()
l1.ref   = 'L1'
l1.value = '53µH'
l1[1] += sw_node        # Input: switching node
l1[2] += dcdc_lv_p     # Output: LV bus rail

# --- C1, C2: Input bulk electrolytic capacitors 560µF 100V ---
make_cap('C1', '560µF', dcdc_hv_p, footprint=FP_CP_D18)
make_cap('C2', '560µF', dcdc_hv_p, footprint=FP_CP_D18)

# --- C5, C27: Output bulk electrolytic capacitors 680µF 35V ---
make_cap('C5',  '680µF', dcdc_lv_p, footprint=FP_CP_D10)
make_cap('C27', '680µF', dcdc_lv_p, footprint=FP_CP_D10)

# --- RV2: 82V MOV varistor — transient/surge suppression across HV input ---
rv2_t = Part('Device', 'R', dest=TEMPLATE)
rv2_t.name, rv2_t.ref_prefix = 'MOV-10D820K', 'RV'
rv2_t.footprint = 'Varistor:RV_Disc_D12mm_W4.3mm_P7.5mm'
rv2_t.pins = [Pin(num='1', name='A'), Pin(num='2', name='K')]

rv2 = rv2_t()
rv2.ref   = 'RV2'
rv2.value = '82V'
rv2['A'] += dcdc_hv_p
rv2['K'] += gnd

# --- C6: 2.2nF 100V C0G snubber across switching node ---
make_cap('C6', '2.2nF', sw_node)

# --- C4: 100nF 100V X7S — high-voltage bypass (0805) ---
make_cap('C4', '100nF', dcdc_hv_p, footprint=FP_C0805)

# --- C17: 10nF input filter cap ---
make_cap('C17', '10nF', dcdc_hv_p)

# --- R56, R61: 1MΩ gate bleed resistors (prevent floating gates) ---
make_res('R56', '1M', hs_drv, sw_node)
make_res('R61', '1M', ls_drv, gnd)

# --- INA186 U3: DC/DC inductor current-sense amplifier ---
# Shunt R6 (2 mΩ) is in the low-side path; U3 amplifies the differential voltage.
ina_t = Part('Device', 'R', dest=TEMPLATE)
ina_t.name, ina_t.ref_prefix = 'INA186', 'U'
ina_t.footprint = FP_SOT363
ina_t.pins = [
    Pin(num='1', name='REF'),    # Reference voltage input (DAC1)
    Pin(num='2', name='GND'),
    Pin(num='3', name='V+'),     # Supply (+3.3V)
    Pin(num='4', name='+IN'),    # In+ (shunt positive, via 10R filter)
    Pin(num='5', name='-IN'),    # In- (shunt negative, via 10R filter)
    Pin(num='6', name='OUT'),    # Output → ADC
]

u3 = ina_t()
u3.ref    = 'U3'
u3.value  = 'INA186'
u3['REF'] += i_dcdc_ref
u3['GND'] += gnd
u3['V+']  += v3v3
u3['+IN'] += shunt_dcdc_p
u3['-IN'] += shunt_dcdc_n
u3['OUT'] += i_dcdc

# R6: DC/DC current-sense shunt 2 mΩ (Bourns CRE2512-FZ-R002E-3)
make_res('R6',  '2m', shunt_dcdc_p, shunt_dcdc_n, footprint=FP_R2512)

# R8, R9: 10Ω input filter resistors on INA186 U3 sense lines
make_res('R8',  '10R', shunt_dcdc_p, u3['+IN'])
make_res('R9',  '10R', shunt_dcdc_n, u3['-IN'])

# C11 (1nF): INA186 output filter cap; C15 (1nF): supply bypass
make_cap('C11', '1nF', i_dcdc)
make_cap('C15', '1nF', v3v3)

# --- Voltage dividers for HV and LV measurement (→ MCU ADC) ---
# V_DCDC_H: R17 (100kΩ) from HV+ → divider node; R14 (2.2kΩ) → GND; C14 filter
# Full-scale: 60V × 2.2/(100+2.2) ≈ 1.29V  (fits 3.3V ADC range)
make_res('R17', '100k', dcdc_hv_p, v_dcdc_h)
make_res('R14', '2.2k', v_dcdc_h,  gnd)
make_cap('C14', '10nF', v_dcdc_h)

# V_DCDC_L: R13 (100kΩ) from LV+ → divider node; R21 (2.2kΩ) → GND; C16 filter
# Full-scale: 30V × 2.2/(100+2.2) ≈ 0.65V  (fits 3.3V ADC range)
make_res('R13', '100k', dcdc_lv_p, v_dcdc_l)
make_res('R21', '2.2k', v_dcdc_l,  gnd)
make_cap('C16', '10nF', v_dcdc_l)


# =============================================================================
# 6.  POWER SUPPLY  (Sheet 3: power-supply.sch)
#     Sub-circuits: supply-rail selection, HV→3.3V SMPS, charge pump, +12V rail
# =============================================================================

# ---------------------------------------------------------------------------
# 6a. Supply Rail Selection
#     Q4 source-follower caps HV input below 60V.
#     T2 switches supply to LV+ once charge pump is running (higher efficiency).
# ---------------------------------------------------------------------------

# Q4 (BUK98180-100A): N-channel source follower, caps supply to ~60V max
q4_t = Part('Device', 'R', dest=TEMPLATE)
q4_t.name, q4_t.ref_prefix = 'BUK98180-100A', 'Q'
q4_t.footprint = FP_SOT223
q4_t.pins = [
    Pin(num='1', name='G'),
    Pin(num='2', name='D'),
    Pin(num='3', name='S'),
]

q4 = q4_t()
q4.ref   = 'Q4'
q4.value = 'BUK98180-100A'
q4['D'] += dcdc_hv_p     # Drain → HV input
q4['S'] += supply_in      # Source → internal supply rail
q4['G'] += dcdc_hv_p     # Gate → HV+ (clamped by D9 zener to limit Vgs)

# D9 (47V zener): clamps Q4 gate-source voltage to ≤47V
d9_t = Part('Device', 'R', dest=TEMPLATE)
d9_t.name, d9_t.ref_prefix = 'SZBZX84C47LT1G', 'D'
d9_t.footprint = FP_SOT23
d9_t.pins = [Pin(num='1', name='A'), Pin(num='2', name='K')]

d9 = d9_t()
d9.ref   = 'D9'
d9.value = '47V'
d9['A'] += supply_in      # Anode to Q4 source (= supply_in)
d9['K'] += q4['G']        # Cathode clamps gate voltage

# R60 (100kΩ): Q4 gate bias from HV+
make_res('R60', '100k', dcdc_hv_p, q4['G'])

# R66 (1kΩ): gate current limit in series with Q4 gate
make_res('R66', '1k', dcdc_hv_p, q4['G'])

# T2 (BC846B NPN): steers supply to LV+ as soon as CP_OUT is active
npn_t = Part('Device', 'R', dest=TEMPLATE)
npn_t.name, npn_t.ref_prefix = 'BC846B', 'T'
npn_t.footprint = FP_SOT23
npn_t.pins = [
    Pin(num='1', name='B'),
    Pin(num='2', name='C'),
    Pin(num='3', name='E'),
]

t2 = npn_t()
t2.ref   = 'T2'
t2.value = 'BC846B'
t2['B'] += net_t2_b
t2['C'] += supply_in
t2['E'] += gnd

make_res('R19', '10k',  cp_out,   net_t2_b)   # CP_OUT drives T2 base via R19
make_res('R65', '100k', net_t2_b, gnd)          # T2 base bleed

# D10 (1N4148): selects LV+ rail into supply when T2 switches
d10 = d_1n4148_t()
d10.ref   = 'D10'
d10.value = '1N4148'
d10['A'] += dcdc_lv_p
d10['K'] += supply_in

# ---------------------------------------------------------------------------
# 6b. HV/LV to 3.3V SMPS — LMR16006X (U4)
#     Input: SUPPLY_INPUT (up to ~60V); Output: +3.3V @ 600mA
# ---------------------------------------------------------------------------
u4_t = Part('Device', 'R', dest=TEMPLATE)
u4_t.name, u4_t.ref_prefix = 'LMR16006X', 'U'
u4_t.footprint = FP_SOT23_6
u4_t.pins = [
    Pin(num='1', name='CB'),    # Bootstrap capacitor
    Pin(num='2', name='GND'),
    Pin(num='3', name='FB'),    # Feedback (voltage setting divider)
    Pin(num='4', name='EN'),    # Enable (tied to Vin → always on)
    Pin(num='5', name='Vin'),
    Pin(num='6', name='SW'),    # Switch node output
]

u4 = u4_t()
u4.ref    = 'U4'
u4.value  = 'LMR16006X'
u4['Vin'] += supply_in
u4['GND'] += gnd
u4['EN']  += supply_in         # Always enabled
u4['CB']  += net_u4_cb
u4['SW']  += net_sw_u4
u4['FB']  += net_fb_u4

# Feedback voltage divider: R23 (33kΩ) upper, R24 (10kΩ) lower → Vout = 3.3V
make_res('R23', '33k', v3v3,     net_fb_u4)
make_res('R24', '10k', net_fb_u4, gnd)

# D1 (SS16FP): free-wheeling/catch diode on U4 SW node
d1_t = Part('Device', 'R', dest=TEMPLATE)
d1_t.name, d1_t.ref_prefix = 'SS16FP', 'D'
d1_t.footprint = 'Diode_SMD:D_PowerDI-123'
d1_t.pins = [Pin(num='1', name='A'), Pin(num='2', name='K')]

d1 = d1_t()
d1.ref   = 'D1'
d1.value = 'SS16FP'
d1['A'] += gnd
d1['K'] += net_sw_u4

# L2 (4.7µH, Murata LQM21PN4R7NGRD): SMPS output inductor
l2_t = Part('Device', 'R', dest=TEMPLATE)
l2_t.name, l2_t.ref_prefix = 'LQM21PN4R7NGRD', 'L'
l2_t.footprint = 'Inductor_SMD:L_0805_2012Metric'
l2_t.pins = [Pin(num='1', name='~'), Pin(num='2', name='~')]

l2 = l2_t()
l2.ref   = 'L2'
l2.value = '4.7µH'
l2[1] += net_sw_u4
l2[2] += v3v3

# L3 (47µH, Tayo Yuden NR6045T470M): output LC filter to reduce ripple
l3_t = Part('Device', 'R', dest=TEMPLATE)
l3_t.name, l3_t.ref_prefix = 'NR6045T470M', 'L'
l3_t.footprint = 'Inductor_THT:Bourns_SRN6045TA'
l3_t.pins = [Pin(num='1', name='~'), Pin(num='2', name='~')]

l3 = l3_t()
l3.ref   = 'L3'
l3.value = '47µH'
l3[1] += v3v3
l3[2] += v3v3    # Both ends on 3.3V rail (common-mode choke function)

# SMPS input/output capacitors
make_cap('C18', '1µF',   supply_in, footprint=FP_C0805)   # Input  bulk (100V)
make_cap('C19', '4.7µF', supply_in, footprint=FP_C1210)   # Input  bulk (100V)
make_cap('C20', '100nF', supply_in)                         # Input  bypass HF
make_cap('C21', '10µF',  v3v3,      footprint=FP_C0805)   # Output bulk
make_cap('C23', '10µF',  v3v3,      footprint=FP_C0805)   # Output bulk

# ---------------------------------------------------------------------------
# 6c. Charge Pump — generates CP_OUT above DCDC_LV+ for HS load switch gate
#     T1 (NPN) inverts CP_PWM; T4 (PNP) level-shifts to LV+; T3 (NPN) drives
#     the AC switch node; D4 (dual Schottky) pumps charge to CP_OUT.
# ---------------------------------------------------------------------------

# T1 (BC846B NPN): CP_PWM input stage / inverter
t1 = npn_t()
t1.ref   = 'T1'
t1.value = 'BC846B'
t1['B'] += net_t1_b
t1['C'] += net_t1_c
t1['E'] += gnd

make_res('R16', '10k', cp_pwm, net_t1_b)   # T1 base resistor from MCU TIM8_CH2

# T4 (BC856B PNP): level-shifts from GND reference to LV+ reference
pnp_t = Part('Device', 'R', dest=TEMPLATE)
pnp_t.name, pnp_t.ref_prefix = 'BC856B', 'T'
pnp_t.footprint = FP_SOT23
pnp_t.pins = [
    Pin(num='1', name='B'),
    Pin(num='2', name='C'),
    Pin(num='3', name='E'),
]

t4 = pnp_t()
t4.ref   = 'T4'
t4.value = 'BC856B'
t4['B'] += net_t1_c       # Base ← T1 collector
t4['C'] += cp_sw           # Collector → charge pump switch node
t4['E'] += dcdc_lv_p      # Emitter → LV+ rail

make_res('R25', '1k', dcdc_lv_p, t4['B'])  # T4 base bias resistor

# T3 (BC846B NPN): charge pump AC switch
t3 = npn_t()
t3.ref   = 'T3'
t3.value = 'BC846B'
t3['B'] += net_t3_b
t3['C'] += cp_sw
t3['E'] += gnd

make_res('R22', '100k', dcdc_lv_p, net_t3_b)  # T3 base bias

# D4 (BAS70-04): dual Schottky diode — pumps charge from cp_sw to CP_OUT
d4_t = Part('Device', 'R', dest=TEMPLATE)
d4_t.name, d4_t.ref_prefix = 'BAS70-04LT1G', 'D'
d4_t.footprint = FP_SOT23
d4_t.pins = [
    Pin(num='1', name='A1'), Pin(num='2', name='K1'),
    Pin(num='3', name='A2'), Pin(num='4', name='K2'),
]

d4 = d4_t()
d4.ref   = 'D4'
d4.value = 'BAS70-04'
d4['A1'] += cp_sw
d4['K1'] += cp_out
d4['A2'] += cp_sw
d4['K2'] += cp_out

# Charge-pump reservoir / coupling caps
make_cap('C24', '100nF', cp_sw)
make_cap('C25', '10nF',  cp_out)

# ---------------------------------------------------------------------------
# 6d. +12V MOSFET Driver Supply — emitter follower from SUPPLY_INPUT
#     T10 (BC846B NPN) + D5 (12V zener) produce a stable +12V for LM5107.
# ---------------------------------------------------------------------------

# T10 (BC846B NPN): emitter follower
t10 = npn_t()
t10.ref   = 'T10'
t10.value = 'BC846B'
t10['B'] += net_t10_b
t10['C'] += supply_in
t10['E'] += v12

make_res('R64', '10k', supply_in, net_t10_b)   # T10 base resistor

# D5 (12V zener): sets emitter voltage of T10 to +12V
d5_t = Part('Device', 'R', dest=TEMPLATE)
d5_t.name, d5_t.ref_prefix = 'SZBZX84C12LT3G', 'D'
d5_t.footprint = FP_SOT23
d5_t.pins = [Pin(num='1', name='A'), Pin(num='2', name='K')]

d5 = d5_t()
d5.ref   = 'D5'
d5.value = '12V'
d5['A'] += gnd
d5['K'] += v12

# Decoupling for +12V rail
make_cap('C40', '1µF',   v12, footprint=FP_C0603)  # Yageo CC0805KKX7R9BB105 (50V X7R)
make_cap('C41', '4.7µF', v12, footprint=FP_C0603)  # Würth 885012107018 (25V X5R)


# =============================================================================
# 7.  LOAD SWITCH  (Sheet 5: load-switch.sch)
#     Q7 (IPA045N10N3G) high-side switch; R62 (2mΩ) shunt; U5 (INA186) sense.
#     T5 pulls Q7 gate low (disables load) when LOAD_DIS is asserted by MCU.
#     Short-circuit protection: COMP2 in MCU monitors U5 output vs Vref_int.
# =============================================================================

# Q7: High-side load switch MOSFET
q7 = q_nmos_t()
q7.ref   = 'Q7'
q7.value = 'IPA045N10N3G'
q7['D'] += load_p          # Drain → LOAD+ output terminal
q7['G'] += meas_lv_p       # Gate driven by CP_OUT via R58 (above LV+ rail)
q7['S'] += shunt_load_p    # Source → current shunt positive terminal

# R58 (100kΩ): couples CP_OUT to Q7 gate
make_res('R58', '100k', cp_out, meas_lv_p)

# D3 (12V zener): Vgs clamp on Q7 (prevents gate overdrive)
d3_t = Part('Device', 'R', dest=TEMPLATE)
d3_t.name, d3_t.ref_prefix = 'SZBZX84C12LT3G', 'D'
d3_t.footprint = FP_SOT23
d3_t.pins = [Pin(num='1', name='A'), Pin(num='2', name='K')]

d3 = d3_t()
d3.ref   = 'D3'
d3.value = '12V'
d3['A'] += shunt_load_p    # Anode → Q7 source (SHUNT_LOAD_P)
d3['K'] += meas_lv_p       # Cathode → Q7 gate (clamps Vgs ≤ 12V)

# C37 (10nF): gate filter capacitor on MEAS_LV+ node
make_cap('C37', '10nF', meas_lv_p)

# T5 (BC846B NPN): gate pull-down when LOAD_DIS is high
t5 = npn_t()
t5.ref   = 'T5'
t5.value = 'BC846B'
t5['B'] += net_t5_b
t5['C'] += meas_lv_p       # Collector pulls Q7 gate low → switch off
t5['E'] += gnd

make_res('R57', '2.2k', load_dis, net_t5_b)    # T5 base drive from MCU GPIOB_2
make_res('R59', '10k',  v3v3,     net_t5_b)    # T5 base pull-up (active-high logic)

# R62: Load current-sense shunt 2 mΩ (Bourns CRE2512)
# Shunt negative side returns to DCDC_LV+ (high-side topology)
make_res('R62', '2m', shunt_load_p, shunt_load_n, footprint=FP_R2512)
shunt_load_n += dcdc_lv_p  # High-side: negative of shunt connects to LV+ bus

# U5: INA186 load current-sense amplifier
u5 = ina_t()
u5.ref    = 'U5'
u5.value  = 'INA186'
u5['REF'] += gnd
u5['GND'] += gnd
u5['V+']  += v3v3
u5['+IN'] += shunt_load_p
u5['-IN'] += shunt_load_n
u5['OUT'] += i_load

make_cap('C28', '100nF', v3v3)          # U5 supply bypass

# R44 (2.2kΩ) + C26 (10nF): low-pass output filter on I_LOAD
make_res('R44', '2.2k', i_load,  adc12_in2)
make_cap('C26', '10nF', adc12_in2)

# Short-circuit comparator threshold: COMP2 uses internal Vref_int (1.202–1.242V).
# R50 (8.2kΩ) + R49 (10kΩ) divide I_LOAD signal; comparator trips at ~3.06V output.
# Equation: Vout_trip = 1.242 × (12 + 8.2) / 8.2 = 3.06V (from schematic annotation)
make_res('R50', '8.2k', i_load,      i_load_comp)
make_res('R49', '10k',  i_load_comp, gnd)
make_cap('C45', '10nF', i_load_comp)            # RC filter (tuning note in schematic)


# =============================================================================
# 8.  CAN INTERFACE  (Sheet 6: can.sch)
#     TCAN334 transceiver, termination resistor/jumper, RJ45 daisy-chain
#     connectors with PoE-style power (10–32V, 600mA like PoE).
# =============================================================================

# U7 (TCAN334): 1 Mbps CAN transceiver
u7_t = Part('Device', 'R', dest=TEMPLATE)
u7_t.name, u7_t.ref_prefix = 'TCAN334', 'U'
u7_t.footprint = FP_SOIC8
u7_t.pins = [
    Pin(num='1', name='TXD'),
    Pin(num='2', name='GND'),
    Pin(num='3', name='VCC'),
    Pin(num='4', name='RXD'),
    Pin(num='5', name='SHDN'),  # Active-low shutdown (tied to GND = always on)
    Pin(num='6', name='CANL'),
    Pin(num='7', name='CANH'),
    Pin(num='8', name='STB'),   # Standby mode control (MCU GPIOB_11)
]

u7 = u7_t()
u7.ref    = 'U7'
u7.value  = 'TCAN334'
u7['TXD']  += can_tx
u7['GND']  += gnd
u7['VCC']  += v3v3
u7['RXD']  += can_rx
u7['SHDN'] += gnd              # Not shutdown
u7['CANL'] += can_l
u7['CANH'] += can_h
u7['STB']  += can_stb

make_cap('C7', '100nF', v3v3)  # U7 VCC bypass (0603, +can config)

# R11 (120Ω): CAN bus termination resistor (1206 for better power rating)
r11_t = Part('Device', 'R', dest=TEMPLATE)
r11_t.name, r11_t.ref_prefix = 'R_120R', 'R'
r11_t.footprint = FP_R1206
r11_t.pins = [Pin(num='1', name='~'), Pin(num='2', name='~')]

r11 = r11_t()
r11.ref   = 'R11'
r11.value = '120R'
r11[1] += can_h
r11[2] += can_l

# JP1: Normally-closed termination jumper — remove to de-terminate this node
jp1_t = Part('Device', 'R', dest=TEMPLATE)
jp1_t.name, jp1_t.ref_prefix = 'Jumper_NC', 'JP'
jp1_t.footprint = 'Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical'
jp1_t.pins = [Pin(num='1', name='A'), Pin(num='2', name='B')]

jp1 = jp1_t()
jp1.ref   = 'JP1'
jp1.value = 'Jumper_NC_Small'
jp1['A'] += can_h
jp1['B'] += can_l

# D2, D6 (NRVTS260ESFT1G): Schottky diodes prevent separate GND loops
d_nrvts_t = Part('Device', 'R', dest=TEMPLATE)
d_nrvts_t.name, d_nrvts_t.ref_prefix = 'NRVTS260ESFT1G', 'D'
d_nrvts_t.footprint = 'Diode_SMD:D_SOD-123'
d_nrvts_t.pins = [Pin(num='1', name='A'), Pin(num='2', name='K')]

d2 = d_nrvts_t()
d2.ref   = 'D2'
d2.value = 'NRVTS260ESFT1G'
d2['A'] += vbus
d2['K'] += can_pwr1

d6 = d_nrvts_t()
d6.ref   = 'D6'
d6.value = 'NRVTS260ESFT1G'
d6['A'] += can_gnd
d6['K'] += gnd

# F2, F3 (500mA polyfuses): protect daisy-chained CAN bus power lines
pf_t = Part('Device', 'R', dest=TEMPLATE)
pf_t.name, pf_t.ref_prefix = '0ZCJ0050AF2E', 'F'
pf_t.footprint = 'Fuse:Fuse_1206_3216Metric'
pf_t.pins = [Pin(num='1', name='A'), Pin(num='2', name='K')]

f2 = pf_t()
f2.ref   = 'F2'
f2.value = '500mA'
f2['A'] += vbus
f2['K'] += can_pwr1

f3 = pf_t()
f3.ref   = 'F3'
f3.value = '500mA'
f3['A'] += vbus
f3['K'] += can_pwr2

# J4, J5 (8P8C RJ45): CAN bus connectors (daisy-chained)
# Pin assignment per schematic: 1=CAN_H, 2=CAN_L, 3=CAN_GND,
#                               4=CAN_PWR1, 5=CAN_PWR2, 6..8=GND/CAN_GND
rj45_t = Part('Device', 'R', dest=TEMPLATE)
rj45_t.name, rj45_t.ref_prefix = '54602-908LF', 'J'
rj45_t.footprint = 'Connector_RJ:RJ45_8P8C_Vertical'
rj45_t.pins = [
    Pin(num='1', name='P1'), Pin(num='2', name='P2'),
    Pin(num='3', name='P3'), Pin(num='4', name='P4'),
    Pin(num='5', name='P5'), Pin(num='6', name='P6'),
    Pin(num='7', name='P7'), Pin(num='8', name='P8'),
]

j4 = rj45_t()
j4.ref     = 'J4'
j4.value   = '8P8C'
j4['P1'] += can_h
j4['P2'] += can_l
j4['P3'] += can_gnd
j4['P4'] += can_pwr1
j4['P5'] += can_pwr2
j4['P6'] += can_gnd
j4['P7'] += gnd
j4['P8'] += gnd

j5 = rj45_t()
j5.ref     = 'J5'
j5.value   = '8P8C'
j5['P1'] += can_h
j5['P2'] += can_l
j5['P3'] += can_gnd
j5['P4'] += can_pwr1
j5['P5'] += can_pwr2
j5['P6'] += can_gnd
j5['P7'] += gnd
j5['P8'] += gnd

# Y1 (8MHz, 0.07%): tight-tolerance crystal required for reliable CAN timing
y1_t = Part('Device', 'R', dest=TEMPLATE)
y1_t.name, y1_t.ref_prefix = 'CSTNE8M00GH5C000R0', 'Y'
y1_t.footprint = 'Crystal:Crystal_Murata_CSTNE'
y1_t.pins = [
    Pin(num='1', name='XIN'),
    Pin(num='2', name='GND'),
    Pin(num='3', name='XOUT'),
]

y1 = y1_t()
y1.ref    = 'Y1'
y1.value  = '8MHz'
y1['XIN']  += osc_in
y1['GND']  += gnd
y1['XOUT'] += osc_out


# =============================================================================
# 9.  MCU  (Sheet 4: mcu.sch)
#     STM32G431CBTx, SPI flash W25Q80DVS, dual-colour status LEDs,
#     UEXT connector, SWD debug, USART header, NTC thermistor.
# =============================================================================

# U2 (STM32G431CBTx): main MCU, LQFP-48
u2_t = Part('Device', 'R', dest=TEMPLATE)
u2_t.name, u2_t.ref_prefix = 'STM32G431CBTx', 'U'
u2_t.footprint = FP_LQFP48
u2_t.pins = [
    Pin(num='1',  name='VBAT'),
    Pin(num='2',  name='PC13'),
    Pin(num='3',  name='PC14-OSC32_IN'),
    Pin(num='4',  name='PC15-OSC32_OUT'),
    Pin(num='5',  name='PF0-OSC_IN'),
    Pin(num='6',  name='PF1-OSC_OUT'),
    Pin(num='7',  name='NRST'),
    Pin(num='8',  name='PA0'),
    Pin(num='9',  name='PA1'),
    Pin(num='10', name='PA2'),
    Pin(num='11', name='PA3'),
    Pin(num='12', name='PA4'),   # DAC1         → I_DCDC_REF
    Pin(num='13', name='PA5'),
    Pin(num='14', name='PA6'),
    Pin(num='15', name='PA7'),   # COMP2_INP    → I_LOAD_COMP
    Pin(num='16', name='PB0'),   # ADC12_IN1    → I_DCDC
    Pin(num='17', name='PB1'),   # ADC12_IN2 (alt) or TIM8_CH3
    Pin(num='18', name='PB2'),   # GPIOB_2      → LOAD_DIS
    Pin(num='19', name='VSSA'),
    Pin(num='20', name='VREF+'),
    Pin(num='21', name='VDDA'),
    Pin(num='22', name='PB10'),  # GPIOB_10     → PWR_INFO
    Pin(num='23', name='VSS'),
    Pin(num='24', name='VDD'),
    Pin(num='25', name='PB11'),  # GPIOB_11     → CAN_STB
    Pin(num='26', name='PB12'),  # ADC1_IN12    → V_DCDC_L / SPI2_CS
    Pin(num='27', name='PB13'),  # TIM1_CH1N    → PWM_LS   / SPI2_SCK
    Pin(num='28', name='PB14'),  # SPI2_MISO
    Pin(num='29', name='PB15'),  # ADC1_IN15    → V_DCDC_H / SPI2_MOSI
    Pin(num='30', name='PA8'),   # TIM1_CH1     → PWM_HS
    Pin(num='31', name='PA9'),   # USART1_TX
    Pin(num='32', name='PA10'),  # USART1_RX
    Pin(num='33', name='PA11'),  # FDCAN1_RX
    Pin(num='34', name='PA12'),  # FDCAN1_TX
    Pin(num='35', name='VSS_2'),
    Pin(num='36', name='VDD_2'),
    Pin(num='37', name='PA13'),  # SWDIO
    Pin(num='38', name='PA14'),  # SWCLK / TIM8_CH2 (CP_PWM in run mode)
    Pin(num='39', name='PA15'),  # SPI1_SSEL (UEXT)
    Pin(num='40', name='PB3'),   # SPI1_SCK  (UEXT)
    Pin(num='41', name='PB4'),   # SPI1_MISO (UEXT)
    Pin(num='42', name='PB5'),   # SPI1_MOSI (UEXT)
    Pin(num='43', name='PB6'),   # I2C1_SCL  (UEXT)
    Pin(num='44', name='PB7'),   # I2C1_SDA  (UEXT)
    Pin(num='45', name='PB8-BOOT0'),
    Pin(num='46', name='PB9'),   # TIM17_CH1 (LED2 PWM)
    Pin(num='47', name='VSS_3'),
    Pin(num='48', name='VDD_3'),
]

u2 = u2_t()
u2.ref   = 'U2'
u2.value = 'STM32G431CBTx'

# Power rails
u2['VDD', 'VDD_2', 'VDD_3'] += v3v3
u2['VSS', 'VSS_2', 'VSS_3'] += gnd
u2['VSSA']   += gnd
u2['VDDA']   += vdda
u2['VREF+']  += vref_p
u2['VBAT']   += v3v3
u2['NRST']   += nrst

# Crystal oscillator pins
u2['PF0-OSC_IN']  += osc_in
u2['PF1-OSC_OUT'] += osc_out

# BOOT0 held low for normal application startup
u2['PB8-BOOT0'] += gnd

# --- MCU signal assignments (from schematic net labels) ---
# DC/DC control
u2['PA8']  += pwm_hs         # TIM1_CH1  → PWM_HS
u2['PB13'] += pwm_ls         # TIM1_CH1N → PWM_LS
u2['PA4']  += i_dcdc_ref     # DAC1      → I_DCDC_REF
u2['PB0']  += i_dcdc         # ADC12_IN1 → I_DCDC (after R10 filter)
u2['PB15'] += v_dcdc_h       # ADC1_IN15 → V_DCDC_H voltage divider
u2['PB12'] += v_dcdc_l       # ADC1_IN12 → V_DCDC_L voltage divider

# Load switch control
u2['PB1']  += adc12_in2       # ADC12_IN2 → filtered load current
u2['PA7']  += i_load_comp    # COMP2_INP → over-current comparator
u2['PB2']  += load_dis       # GPIOB_2   → LOAD_DIS → T5 base
u2['PA14'] += cp_pwm         # TIM8_CH2  → CP_PWM  → charge pump

# CAN interface
u2['PA11'] += can_rx          # FDCAN1_RX
u2['PA12'] += can_tx          # FDCAN1_TX
u2['PB11'] += can_stb         # GPIOB_11  → CAN_STB

# Peripheral power monitor
u2['PB10'] += pwr_info        # GPIOB_10  → PWR_INFO → J6

# SPI2 (flash U6)
u2['PB14'] += spi2_miso
u2['PB5']  += spi2_mosi       # (routed also as SPI1_MOSI for UEXT)
u2['PB3']  += spi2_sck        # (routed also as SPI1_SCK  for UEXT)
u2['PA5']  += spi2_cs

# USART1 (debug / UEXT TXD/RXD)
u2['PA9']  += usart1_tx
u2['PA10'] += usart1_rx

# I2C1 (UEXT)
u2['PB6']  += i2c1_scl
u2['PB7']  += i2c1_sda

# SWD debug
u2['PA13'] += swdio
# PA14 shared: SWD clock in debug mode, TIM8_CH2 (CP_PWM) in run mode
u2['PA14'] += swclk

make_cap('C3', '1µF', dcdc_hv_p, dcdc_hv_n, footprint=FP_C0805)
make_res('R18', '5.6k', v_dcdc_l, gnd)

# MCU decoupling capacitors (100nF × 4 + 2.2µF × 2 + 10nF VREF)
make_cap('C29', '100nF', v3v3)
make_cap('C30', '100nF', v3v3)
make_cap('C31', '100nF', v3v3)
make_cap('C32', '100nF', v3v3)
make_cap('C33', '2.2µF', v3v3)
make_cap('C34', '10nF',  v3v3)
make_cap('C48', '100nF', v3v3)
make_cap('C49', '2.2µF', v3v3)

# VDDA / VREF analogue supply filtering
# R40 (47Ω): ferrite-bead substitute from +3.3V → VDDA
# R27 (47Ω): series filter from +3.3V → VREF+
make_res('R40', '47R', v3v3, vdda)
make_res('R27', '47R', v3v3, vref_p)
make_cap('C39', '2.2µF', vdda)   # VDDA bulk
make_cap('C9',  '100nF', vdda)   # VDDA HF bypass

# BOOT0 pull-down and reset cap
make_res('R32', '47R', gnd, u2['PB8-BOOT0'])   # BOOT0 pull-down
make_cap('C13', '100nF', nrst)                  # NRST debounce cap

# SPI line series resistors (R28, from 47R group)
make_res('R28', '47R', spi2_miso, spi2_miso)   # SPI EMI filter (stub)

# --- U6 (W25Q80DVS): 8 Mbit SPI flash memory ---
u6_t = Part('Device', 'R', dest=TEMPLATE)
u6_t.name, u6_t.ref_prefix = 'W25Q80DVS', 'U'
u6_t.footprint = FP_SOIC8
u6_t.pins = [
    Pin(num='1', name='CS'),
    Pin(num='2', name='DO'),        # MISO
    Pin(num='3', name='IO2'),       # /WP tied high
    Pin(num='4', name='GND'),
    Pin(num='5', name='DI'),        # MOSI
    Pin(num='6', name='CLK'),
    Pin(num='7', name='IO3'),       # /HOLD tied high
    Pin(num='8', name='VCC'),
]

u6 = u6_t()
u6.ref   = 'U6'
u6.value = 'W25Q80DVS'
u6['CS']  += spi2_cs
u6['DO']  += spi2_miso
u6['IO2'] += v3v3          # /WP tied high (write protect disabled)
u6['GND'] += gnd
u6['DI']  += spi2_mosi
u6['CLK'] += spi2_sck
u6['IO3'] += v3v3          # /HOLD tied high
u6['VCC'] += v3v3

make_cap('C22', '100nF', v3v3)   # U6 VCC bypass (0603)

# --- Status LEDs LED1, LED2 (dual-colour PLCC4: green + red) ---
led_t = Part('Device', 'R', dest=TEMPLATE)
led_t.name, led_t.ref_prefix = 'LTST-E682KRKGWT', 'LED'
led_t.footprint = 'LED_SMD:LED_Avago_PLCC4_3.2x2.8mm_CW'
led_t.pins = [
    Pin(num='1', name='K1'),   # Cathode colour 1
    Pin(num='2', name='A1'),   # Anode   colour 1
    Pin(num='3', name='K2'),   # Cathode colour 2
    Pin(num='4', name='A2'),   # Anode   colour 2
]

led1 = led_t()
led1.ref   = 'LED1'
led1.value = 'LED_Dual_PLCC4'
led1['K1'] += gnd
led1['A1'] += net_led_g1
led1['K2'] += gnd
led1['A2'] += net_led_r1

led2 = led_t()
led2.ref   = 'LED2'
led2.value = 'LED_Dual_PLCC4'
led2['K1'] += gnd
led2['A1'] += net_led_g2
led2['K2'] += gnd
led2['A2'] += net_led_r2

# LED current-limiting resistors (1kΩ from +3.3V to each anode net)
make_res('R29', '1k', v3v3, net_led_g1)
make_res('R26', '1k', v3v3, net_led_r1)

# --- NTC thermistor RT1 (10k, TDK NTCG163JF103FT1S) for temperature monitoring ---
rt1_t = Part('Device', 'R', dest=TEMPLATE)
rt1_t.name, rt1_t.ref_prefix = 'NTCG163JF103FT1S', 'RT'
rt1_t.footprint = 'Resistor_THT:C_Disc_D3.8mm_W2.6mm_P2.50mm'
rt1_t.pins = [Pin(num='1', name='~'), Pin(num='2', name='~')]

rt1 = rt1_t()
rt1.ref   = 'RT1'
rt1.value = '10k'
rt1[1] += v3v3
rt1[2] += net_rt1

# R7 (10k): fixed resistor in NTC voltage divider → MCU ADC
make_res('R7',  '10k', net_rt1, gnd)
# R15 (1M): gate / pull-down bleed for temperature sense node
make_res('R15', '1M',  net_rt1, gnd)

# --- I2C pull-up resistors (R31, R20 from 2.2k group) ---
make_res('R31', '2.2k', v3v3, i2c1_scl)
make_res('R20', '2.2k', v3v3, i2c1_sda)

# --- ADC input filter on I_DCDC_REF / DAC output ---
make_res('R10', '1k', i_dcdc,     u2['PB0'])   # R10: DAC/ADC output series resistor

# --- SWD Debug connector (SWD1, 1×5 pin header) ---
swd1_t = Part('Device', 'R', dest=TEMPLATE)
swd1_t.name, swd1_t.ref_prefix = 'ST_Nucleo_SWD', 'SWD'
swd1_t.footprint = 'Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Vertical'
swd1_t.pins = [
    Pin(num='1', name='VCC'),
    Pin(num='2', name='SWCLK'),
    Pin(num='3', name='GND'),
    Pin(num='4', name='SWDIO'),
    Pin(num='5', name='NRST'),
]

swd1 = swd1_t()
swd1.ref    = 'SWD1'
swd1.value  = 'ST_Nucleo_SWD'
swd1['VCC']   += v3v3
swd1['SWCLK'] += swclk
swd1['GND']   += gnd
swd1['SWDIO'] += swdio
swd1['NRST']  += nrst

# --- USART header P1 (1×2 pin header for TX/RX) ---
p1_t = Part('Device', 'R', dest=TEMPLATE)
p1_t.name, p1_t.ref_prefix = 'CONN_01X02', 'P'
p1_t.footprint = 'Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical'
p1_t.pins = [Pin(num='1', name='TX'), Pin(num='2', name='RX')]

p1 = p1_t()
p1.ref   = 'P1'
p1.value = 'CONN_01X02'
p1['TX'] += usart1_tx
p1['RX'] += usart1_rx

# --- UEXT connector J7 (2×5 box header, Olimex standard) ---
j7_t = Part('Device', 'R', dest=TEMPLATE)
j7_t.name, j7_t.ref_prefix = 'UEXT', 'J'
j7_t.footprint = 'Connector_IDC:IDC_Header_Straight_2x05_Pitch2.54mm'
j7_t.pins = [
    Pin(num='1',  name='3V3'),
    Pin(num='2',  name='GND'),
    Pin(num='3',  name='TXD'),
    Pin(num='4',  name='RXD'),
    Pin(num='5',  name='SCL'),
    Pin(num='6',  name='SDA'),
    Pin(num='7',  name='MISO'),
    Pin(num='8',  name='MOSI'),
    Pin(num='9',  name='SCK'),
    Pin(num='10', name='SSEL'),
]

j7 = j7_t()
j7.ref    = 'J7'
j7.value  = 'UEXT'
j7['3V3']  += v3v3
j7['GND']  += gnd
j7['TXD']  += usart1_tx
j7['RXD']  += usart1_rx
j7['SCL']  += i2c1_scl
j7['SDA']  += i2c1_sda
j7['MISO'] += spi2_miso
j7['MOSI'] += spi2_mosi
j7['SCK']  += spi2_sck
j7['SSEL'] += spi2_cs


# =============================================================================
# 10.  EXTERNAL CONNECTORS & PROTECTION  (Sheet 1: top-level)
# =============================================================================

# --- Main I/O connectors: Phoenix Contact MKDS 5/2-9.5 (9.5mm pitch, 2-pin) ---
mkds_t = Part('Device', 'R', dest=TEMPLATE)
mkds_t.name, mkds_t.ref_prefix = 'Phoenix_MKDS_5-2', 'J'
mkds_t.footprint = (
    'TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS_5-2-9,5_'
    '1x02_P9.50mm_Horizontal'
)
mkds_t.pins = [Pin(num='1', name='+'), Pin(num='2', name='-')]

j1 = mkds_t()
j1.ref   = 'J1'
j1.value = 'MKDS 5/ 2-9,5'
j1['+'] += dcdc_hv_p    # Solar panel positive (MPPT input: up to 60V)
j1['-'] += dcdc_hv_n    # Solar panel negative

j2 = mkds_t()
j2.ref   = 'J2'
j2.value = 'MKDS 5/ 2-9,5'
j2['+'] += bat_p         # Battery positive (after fuse F1 / holder XF1)
j2['-'] += gnd

j3 = mkds_t()
j3.ref   = 'J3'
j3.value = 'MKDS 5/ 2-9,5'
j3['+'] += load_p        # Load output positive (20A, high-side switched by Q7)
j3['-'] += gnd

# --- XF1: Keystone 3557-2 blade fuse holder (25A) ---
xf1_t = Part('Device', 'R', dest=TEMPLATE)
xf1_t.name, xf1_t.ref_prefix = 'Keystone_3557-2', 'XF'
xf1_t.footprint = 'Fuse:Keystone-Fuse-3557-2'
xf1_t.pins = [Pin(num='1', name='LINE'), Pin(num='2', name='LOAD')]

xf1 = xf1_t()
xf1.ref    = 'XF1'
xf1.value  = 'Fuse_Holder'
xf1['LINE'] += dcdc_lv_p   # Fuse holder input from DC/DC output
xf1['LOAD'] += bat_p        # Fuse holder output to battery connector J2

# --- F1: Littelfuse 0287025.PXCN  25A plug-in blade fuse (sits in XF1) ---
f1_t = Part('Device', 'R', dest=TEMPLATE)
f1_t.name, f1_t.ref_prefix = '0287025.PXCN', 'F'
f1_t.footprint = 'Fuse:Fuse_Littelfuse_595_Series_Blade'
f1_t.pins = [Pin(num='1', name='A'), Pin(num='2', name='K')]

f1 = f1_t()
f1.ref   = 'F1'
f1.value = '25A'
f1['A'] += dcdc_lv_p
f1['K'] += bat_p

# C8 (10nF): decoupling cap near battery connector / after fuse
make_cap('C8', '10nF', bat_p)

# --- J6: JST PH B3B-PH-K 3-pin — monitored peripheral power supply ---
# Provides a switched +V supply (from CP_OUT) to e.g. a GSM module.
# PWR_INFO pin lets the MCU know whether peripheral is powered.
j6_t = Part('Device', 'R', dest=TEMPLATE)
j6_t.name, j6_t.ref_prefix = 'B3B-PH-K', 'J'
j6_t.footprint = 'Connector_JST:JST_PH_B3B-PH-K_1x03_P2.00mm_Vertical'
j6_t.pins = [
    Pin(num='1', name='PWR_INFO'),
    Pin(num='2', name='VCC'),
    Pin(num='3', name='GND'),
]

j6 = j6_t()
j6.ref    = 'J6'
j6.value  = 'JST PH'
j6['PWR_INFO'] += pwr_info
j6['VCC']      += cp_out   # Switched supply (on when charge pump is running)
j6['GND']      += gnd

# R30 (100k): PWR_INFO pull-down keeps MCU pin defined when J6 is disconnected
make_res('R30', '100k', pwr_info, gnd)


# =============================================================================
# 11.  OUTPUT: KiCad netlist  +  CSV BOM
# =============================================================================

def generate_csv_bom(filename: str = 'mppt_2420_hc_BOM.csv') -> None:
    """
    Walk every instantiated (non-template) part, group identical entries by
    (part name, value, footprint), sort reference designators naturally, and
    write a Digikey-style BOM CSV to *filename*.
    """
    bom_groups: dict = defaultdict(list)

    for part in default_circuit.parts:  # type: ignore[attr-defined]
        # Skip TEMPLATE parts and anything without a reference designator
        if getattr(part, 'dest', None) == TEMPLATE:  # type: ignore[name-defined]
            continue
        ref = getattr(part, 'ref', None)
        if not ref:
            continue

        key = (
            getattr(part, 'name',      ''),
            getattr(part, 'value',     ''),
            getattr(part, 'footprint', ''),
        )
        bom_groups[key].append(ref)

    with open(filename, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([
            'Quantity', 'Reference(s)', 'Value', 'Part Name', 'Footprint',
        ])
        for (name, value, footprint), refs in sorted(bom_groups.items()):
            refs.sort()
            writer.writerow([
                len(refs),
                ', '.join(refs),
                value,
                name,
                footprint,
            ])

    print(f'✅  BOM   saved  →  {filename}')


generate_netlist(filename='mppt_2420_hc_skidl.net')
print('✅  Netlist saved  →  mppt_2420_hc_skidl.net')
generate_csv_bom(filename='mppt_2420_hc_BOM.csv')
