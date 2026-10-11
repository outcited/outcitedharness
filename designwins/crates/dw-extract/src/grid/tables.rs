//! Constant tables — `key_features_grid.py` data, verbatim.
//!
//! Every table here is copied from the reference without adjustment; the
//! `build_grid` logic in `super` reads them exactly as the Python does.

/// `key_features_grid.GROUPS` — the fixed ten groups, in contract order.
pub const GROUPS: &[(&str, &str)] = &[
    ("processing", "Processing"),
    ("memory", "Memory"),
    ("peripherals", "Peripherals"),
    ("connectivity", "Connectivity"),
    ("analogue", "Analogue"),
    ("timers_pwm_control", "Timers, PWM & Control"),
    ("power_clock_reset", "Power, Clock & Reset"),
    ("security_safety_identity", "Security, Safety & Identity"),
    ("io_package_environment", "I/O, Package & Environment"),
    ("graphics_vision_touch_hmi", "Graphics, Vision, Touch & HMI"),
];

/// `CLASS_GROUPS` — chapter/feature class -> group(s); order is the
/// reference's tuple order and is preserved where it iterates.
pub const CLASS_GROUPS: &[(&str, &[&str])] = &[
    ("cpu_core", &["processing"]),
    ("npu", &["processing"]),
    ("flash", &["memory"]),
    ("sram", &["memory"]),
    ("eeprom", &["memory"]),
    ("memory_map", &["memory"]),
    ("dma", &["peripherals"]),
    ("interrupts", &["peripherals"]),
    ("debug", &["peripherals"]),
    ("safety", &["peripherals", "security_safety_identity"]),
    ("usart", &["connectivity"]),
    ("spi", &["connectivity"]),
    ("i2c", &["connectivity"]),
    ("i3c", &["connectivity"]),
    ("can", &["connectivity"]),
    ("lin", &["connectivity"]),
    ("usb", &["connectivity"]),
    ("ethernet", &["connectivity"]),
    ("sdmmc", &["connectivity"]),
    ("xspi", &["connectivity"]),
    ("fmc", &["connectivity"]),
    ("wireless", &["connectivity"]),
    ("adc", &["analogue"]),
    ("temp_sensor", &["analogue"]),
    ("dac", &["analogue"]),
    ("comparator", &["analogue"]),
    ("opamp", &["analogue"]),
    ("timer", &["timers_pwm_control"]),
    ("lptim", &["timers_pwm_control"]),
    ("hrtim", &["timers_pwm_control"]),
    ("rtc", &["timers_pwm_control"]),
    (
        "watchdog",
        &["timers_pwm_control", "security_safety_identity"],
    ),
    ("power", &["power_clock_reset"]),
    ("reset_clock", &["power_clock_reset"]),
    ("crypto", &["security_safety_identity"]),
    ("trustzone", &["processing", "security_safety_identity"]),
    ("gpio", &["io_package_environment"]),
    ("display", &["graphics_vision_touch_hmi"]),
    ("dcmi", &["graphics_vision_touch_hmi"]),
    ("touch", &["graphics_vision_touch_hmi"]),
    ("audio", &["connectivity"]),
];

/// `INSTANCE_CLASS` — instance grammar class -> (class, display label).
pub const INSTANCE_CLASS: &[(&str, &str, &str)] = &[
    ("TIM", "timer", "Timers (TIM)"),
    ("LPTIM", "lptim", "Low-power timers (LPTIM)"),
    ("HRTIM", "hrtim", "High-resolution timer (HRTIM)"),
    ("TIMG", "timer", "General-purpose timers (TIMG)"),
    ("TIMA", "timer", "Advanced timers (TIMA)"),
    ("EPWM", "timer", "ePWM modules"),
    ("ECAP", "timer", "eCAP modules"),
    ("EQEP", "timer", "eQEP modules"),
    ("USART", "usart", "USART"),
    ("UART", "usart", "UART"),
    ("LPUART", "usart", "Low-power UART (LPUART)"),
    ("SCI", "usart", "Serial Communications Interface (SCI)"),
    ("SPI", "spi", "SPI"),
    ("I2S", "spi", "I2S"),
    ("SAI", "audio", "Serial audio interface (SAI)"),
    ("I2C", "i2c", "I2C"),
    ("I3C", "i3c", "I3C"),
    ("FDCAN", "can", "CAN"),
    ("CAN", "can", "CAN"),
    ("MCAN", "can", "CAN-FD (MCAN)"),
    ("DCAN", "can", "DCAN"),
    ("ADC", "adc", "ADC"),
    ("DAC", "dac", "DAC"),
    ("COMP", "comparator", "Analog comparator (COMP)"),
    ("OPAMP", "opamp", "Operational amplifier (OPAMP)"),
    ("DMA", "dma", "DMA controllers"),
    ("GPIO", "gpio", "GPIO ports"),
    ("USB", "usb", "USB"),
    ("ETH", "ethernet", "Ethernet"),
    ("SDMMC", "sdmmc", "SDMMC"),
    ("OCTOSPI", "xspi", "Octo/Quad-SPI"),
    ("FMC", "fmc", "Flexible memory controller (FMC/FSMC)"),
    ("RTC", "rtc", "RTC"),
    ("IWDG", "watchdog", "Watchdogs"),
    ("AES", "crypto", "Cryptographic accelerators"),
    ("LTDC", "display", "Display / camera controllers"),
    ("UCPD", "usb", "USB Type-C / Power Delivery (UCPD)"),
    ("TSC", "touch", "Touch sensing controller (TSC)"),
    ("RSPI", "spi", "Serial Peripheral Interface (RSPI)"),
    ("RIIC", "i2c", "I2C bus interface (RIIC)"),
    ("MTU", "timer", "Multi-function timer pulse unit (MTU)"),
    ("TPU", "timer", "16-bit timer pulse unit (TPU)"),
    ("CMT", "timer", "Compare match timer (CMT)"),
    ("TMR", "timer", "8-bit timer (TMR)"),
    ("GPT32", "timer", "General PWM timer (GPT)"),
    ("AGT", "timer", "Asynchronous general-purpose timer (AGT)"),
    ("S12AD", "adc", "12-bit A/D converter (S12AD)"),
    ("RSCAN", "can", "CAN (RSCAN)"),
    ("DMAC", "dma", "DMA controller (DMAC)"),
    ("DTC", "dma", "Data transfer controller (DTC)"),
    ("ELC", "interrupts", "Event link controller (ELC)"),
    ("MIBSPI", "spi", "Multi-buffered SPI (MibSPI)"),
    ("N2HET", "timer", "High-end timer (N2HET)"),
    ("RTI", "timer", "Real-time interrupt module (RTI)"),
    ("GIO", "gpio", "GIO ports"),
    ("MIBADC", "adc", "Multi-buffered ADC (MibADC)"),
    ("LIN", "lin", "LIN"),
    ("EMAC", "ethernet", "Ethernet MAC (EMAC)"),
    ("TPM", "timer", "Timer/PWM module (TPM)"),
    ("FTM", "timer", "FlexTimer module (FTM)"),
    ("PIT", "timer", "Periodic interrupt timer (PIT)"),
    ("LPTMR", "lptim", "Low-power timer (LPTMR)"),
    ("LPIT", "lptim", "Low-power periodic interrupt timer (LPIT)"),
    ("CTIMER", "timer", "Standard counter/timers (CTIMER)"),
    ("SCTIMER", "timer", "SCTimer/PWM"),
    ("TSI", "touch", "Touch sensing input (TSI)"),
    ("CMP", "comparator", "Analog comparator (CMP)"),
    ("LPI2C", "i2c", "Low-power I2C (LPI2C)"),
    ("LPSPI", "spi", "Low-power SPI (LPSPI)"),
    ("FLEXCOMM", "usart", "Flexcomm serial interfaces"),
    ("FLEXCAN", "can", "FlexCAN"),
    ("ENET", "ethernet", "Ethernet (ENET)"),
    ("SDHC", "sdmmc", "SD host controller (SDHC)"),
    ("FLEXIO", "usart", "FlexIO"),
    ("FLEXSPI", "xspi", "FlexSPI"),
    ("LLWU", "power", "Low-leakage wakeup unit (LLWU)"),
    ("DMAMUX", "dma", "DMA channel mux (DMAMUX)"),
    ("EWM", "watchdog", "External watchdog monitor (EWM)"),
    ("CRC", "safety", "CRC"),
    ("LCDK", "display", "LCD controller"),
    ("MIPI", "display", "MIPI DSI/CSI"),
    ("LPADC", "adc", "Low-power ADC (LPADC)"),
    ("TIMER", "timer", "Timer/Counter (TIMER)"),
    ("WTIMER", "timer", "Wide Timer/Counter (WTIMER)"),
    ("LETIMER", "lptim", "Low Energy Timer (LETIMER)"),
    ("LEUART", "usart", "Low Energy UART (LEUART)"),
    ("PCNT", "timer", "Pulse Counter (PCNT)"),
    ("ACMP", "comparator", "Analog Comparator (ACMP)"),
    ("IDAC", "dac", "Current DAC (IDAC)"),
    ("VDAC", "dac", "Voltage DAC (VDAC)"),
    ("RTCC", "rtc", "Real Time Counter and Calendar (RTCC)"),
    ("CRYOTIMER", "lptim", "Ultra Low Energy Timer (CRYOTIMER)"),
    ("LDMA", "dma", "Linked DMA (LDMA)"),
    ("GPCRC", "safety", "General Purpose CRC (GPCRC)"),
    ("PRS", "interrupts", "Peripheral Reflex System (PRS)"),
    ("CSEN", "touch", "Capacitive Sense (CSEN)"),
    ("LESENSE", "touch", "Low Energy Sensor Interface (LESENSE)"),
    ("WDOG", "watchdog", "Watchdog (WDOG)"),
    ("PCA", "timer", "Programmable Counter Array (PCA)"),
    ("SMB", "i2c", "SMBus (SMB)"),
];

/// `_SECTION_GROUP_DIRECT` — normalised heading -> group (exact match).
pub const SECTION_GROUP_DIRECT: &[(&str, &str)] = &[
    ("memory", "memory"),
    ("memories", "memory"),
    ("connectivity", "connectivity"),
    ("communication interfaces", "connectivity"),
    ("communications", "connectivity"),
    ("analog", "analogue"),
    ("analogue", "analogue"),
    ("advanced analog features", "analogue"),
    ("analog peripherals", "analogue"),
    ("timers", "timers_pwm_control"),
    ("timers output compare input capture", "timers_pwm_control"),
    ("high speed pwm", "timers_pwm_control"),
    ("safety", "security_safety_identity"),
    ("security", "security_safety_identity"),
    ("security and encryption", "security_safety_identity"),
    (
        "qualification and class b support",
        "security_safety_identity",
    ),
    ("human machine interface hmi", "graphics_vision_touch_hmi"),
    ("human machine interface", "graphics_vision_touch_hmi"),
    ("graphics", "graphics_vision_touch_hmi"),
    ("power management", "power_clock_reset"),
    ("clock management", "power_clock_reset"),
    ("power", "power_clock_reset"),
    ("clocks", "power_clock_reset"),
    ("low power", "power_clock_reset"),
    ("operating conditions", "power_clock_reset"),
    (
        "operating temperature and packages",
        "io_package_environment",
    ),
    ("packages", "io_package_environment"),
    ("input output", "io_package_environment"),
    ("general purpose i o ports", "io_package_environment"),
    ("system and power management", "power_clock_reset"),
    ("multiple clock sources", "power_clock_reset"),
    ("clock sources", "power_clock_reset"),
    ("reset and clock control", "power_clock_reset"),
    ("power supply", "power_clock_reset"),
    ("arm cortex m33 core", "processing"),
    ("arm cortex m4 core", "processing"),
    ("arm cortex m23 core", "processing"),
    ("arm cortex m85 core", "processing"),
    ("arm cortex m0 core", "processing"),
    ("cpu core", "processing"),
    ("event link", "peripherals"),
    ("direct memory access dma", "peripherals"),
    ("dma", "peripherals"),
    ("debugger development support", "peripherals"),
    ("core", "processing"),
    ("cpu", "processing"),
];

/// `_SECTION_PATTERNS` — checked in order after the exact table.
pub const SECTION_PATTERNS: &[(&str, &str)] = &[
    (r"^timers?\b", "timers_pwm_control"),
    (r"^communication", "connectivity"),
    (
        r"^up to (?:fast )?i o ports?$|^i os?$|^i o ports?$|^i o$",
        "io_package_environment",
    ),
    (
        r"^clock reset and supply management$|^clocks? and reset|^reset and clock|^supply and reset|^clock(?:ing)? and power",
        "power_clock_reset",
    ),
    (
        r"^operating voltage$|^voltage range$|^supply voltage",
        "power_clock_reset",
    ),
    (r"^debug(?: mode| and trace)?$|^debug", "peripherals"),
    (
        r"^(?:arm )?cortex m\d+\S* core$|^(?:arm|risc v) .*core$|^processor$|^cpu$|^core$",
        "processing",
    ),
    (r"^analog(?:ue)?", "analogue"),
    (r"^memor(?:y|ies)", "memory"),
    (
        r"^security|^safety|^cryptograph",
        "security_safety_identity",
    ),
    (
        r"^graphics|^display|^human machine|^hmi$|^touch",
        "graphics_vision_touch_hmi",
    ),
    (
        r"^packages?|^operating temperature|^temperature range",
        "io_package_environment",
    ),
    (r"^performance$", "processing"),
    (
        r"^advanced motion control$|^motion control",
        "timers_pwm_control",
    ),
    (
        r"^package information$|^packaging",
        "io_package_environment",
    ),
];

/// `_MIXED_SECTIONS` — headings whose bullets decide the group.
pub const MIXED_SECTIONS: &[&str] = &[
    "system and power management",
    "system",
    "peripherals",
    "other",
    "others",
    "miscellaneous",
    "additional features",
    "description",
];

/// `_DUAL_HOME_CLASSES` — classes that keep both heading and bullet homes.
pub const DUAL_HOME_CLASSES: &[&str] = &["watchdog", "safety", "trustzone", "crypto"];

/// `_CLASS_OVER_SECTION` — in a features table these classes win over heading.
pub const CLASS_OVER_SECTION: &[&str] = &[
    "flash",
    "sram",
    "eeprom",
    "memory_map",
    "debug",
    "dma",
    "package",
    "temp_sensor",
];

/// `_SIZING_UNITS` (lowercased, `rstrip("s")` compared).
pub const SIZING_UNITS: &[&str] = &[
    "mbyte", "kbyte", "mb", "kb", "mbit", "kbit", "mhz", "khz", "ghz", "v", "channel", "i/o",
    "pin", "°c",
];

/// `_STANDALONE_LEAF_CLASSES`.
pub const STANDALONE_LEAF_CLASSES: &[&str] = &[
    "temp_sensor",
    "dma",
    "rtc",
    "watchdog",
    "safety",
    "comparator",
    "dac",
    "adc",
    "touch",
    "display",
    "usb",
    "can",
    "ethernet",
    "crypto",
    "opamp",
];

/// `_STOP_TOKENS` — tokens ignored by label tokenisation.
pub const STOP_TOKENS: &[&str] = &[
    "module",
    "modules",
    "with",
    "and",
    "the",
    "of",
    "a",
    "an",
    "for",
    "x",
    "×",
    "interface",
    "interfaces",
    "controller",
    "controllers",
    "unit",
    "units",
    "core",
    "cpu",
    "bus",
    "up",
    "to",
];

/// `_SOURCE_RANK` — label specificity by source.
pub const SOURCE_RANK: &[(&str, i32)] = &[
    ("features", 3),
    ("features_heading", 3),
    ("memory", 2),
    ("chapter", 1),
];

/// `_PROSE_GROUP` — prose fact kind -> (group, class).
pub const PROSE_GROUP: &[(&str, &str, &str)] = &[
    ("core", "processing", "cpu_core"),
    ("max_frequency", "processing", "cpu_core"),
    ("supply_range", "power_clock_reset", "power"),
    ("temperature_range", "io_package_environment", "gpio"),
    ("packages", "io_package_environment", "gpio"),
];

/// `VENDOR_DOMAIN` — vendor name -> domain.
pub const VENDOR_DOMAIN: &[(&str, &str)] = &[
    ("ti", "ti.com"),
    ("texas instruments", "ti.com"),
    ("st", "st.com"),
    ("stmicroelectronics", "st.com"),
    ("stm", "st.com"),
    ("microchip", "microchip.com"),
    ("atmel", "microchip.com"),
    ("renesas", "renesas.com"),
    ("nxp", "nxp.com"),
    ("freescale", "nxp.com"),
    ("gigadevice", "gigadevice.com"),
    ("gd", "gigadevice.com"),
    ("silabs", "silabs.com"),
    ("silicon labs", "silabs.com"),
    ("infineon", "infineon.com"),
    ("cypress", "infineon.com"),
    ("nuvoton", "nuvoton.com"),
    ("espressif", "espressif.com"),
    ("adi", "analog.com"),
    ("analog devices", "analog.com"),
    ("ambiq", "ambiq.com"),
    ("nordic", "nordicsemi.com"),
];

/// `_VENDOR_HINTS` — document id / scope / filename probe patterns.
pub const VENDOR_HINTS: &[(&str, &str)] = &[
    (
        r"^(?:RM|UM|PM|DS|AN|ES)\d{4}$|^STM32|^STM8|^rm\d{4}|^(?:L\d{4}|L\d{4}[A-Z]?|ST1S\d+|STSPIN\w*|STM|VIPER|L6\d{3}|L7\d{3}|L5\d{3}|L9\d{3})\b",
        "st.com",
    ),
    (
        r"^(?:SLAU|SLAS|SPRU|SPRS|SPNU|SPNS|SPNZ|SLVS|SLLS|SPMU|SPMS|SPRUI|SPRUH|SPRUG|SPRUF|SLAA|SWRU|SWRS|TIDU)[A-Z0-9]+|^(?:TMS320|TMS570|MSP430|MSPM0|MSPM33|TM4C|AM2|AM6|RM4|RM5|CC\d{4}|Tiva|Concerto|F28M|Jacinto|PRU\b)",
        "ti.com",
    ),
    (
        r"^R01(?:UH|DS|AN|TU)\d{4}|^REN_|^(?:RA\d|RX\d|RL78|RZ|RH850|RE01|R7F|R5F|DA1\d{4})",
        "renesas.com",
    ),
    (
        r"^DS\d{8}[A-Z]?$|^DS\d{5}[A-Z]$|^(?:PIC|dsPIC|AT(?:SAM|mega|tiny|xmega)|SAM[A-Z]\d|SAM9|CEC1\d{2}|MEC1\d{2}|AVR)",
        "microchip.com",
    ),
    (r"^GD32", "gigadevice.com"),
    (
        r"^(?:EFM32|EFR32|EFM8|C8051|SiM3|Si\d{4}|BGM|MGM|EZR32)",
        "silabs.com",
    ),
    (
        r"^(?:MK|MKL|MKV|MKE|MKW|LPC|MIMXRT|IMXRT|i\.?MX|MCX|S32|KL\d|K\d{2}P\d{2,3}M|KE\d|KV\d|KW\d|KBTLDR|DRM\d{3}|FT10RM|Kinetis|Freescale|QN9|JN5|RW61)",
        "nxp.com",
    ),
    (
        r"^(?:XMC|PSoC|PSOC|CY8C|CYW|TLE|AURIX|TC2|TC3|TC4|infineon|infns)",
        "infineon.com",
    ),
    (r"^nRF\d{4,5}", "nordicsemi.com"),
];

/// `_MEMORY_KIND` — memory kind probe patterns, checked in order.
pub const MEMORY_KIND: &[(&str, &str)] = &[
    ("eeprom", r"(?i)\beeprom\b"),
    ("data_flash", r"(?i)\bdata\s+flash\b"),
    (
        "flash",
        r"(?i)\bflash\b|\bprogram\s+memory\b|\bcode\s+memory\b",
    ),
    ("rom", r"(?i)\bROM\b|\bboot\s+rom\b|\bmask\s+rom\b"),
    ("sram", r"(?i)\bs?ram\b|\bTCM\b|\bCCM\b"),
];

/// `_SECURITY_VOCAB` — security function probe patterns.
pub const SECURITY_VOCAB: &[(&str, &str)] = &[
    ("aes", r"\bAES(?:[- ]?(?:128|192|256))?\b"),
    ("sha", r"\bSHA(?:[- ]?(?:1|2|224|256|384|512))?\b"),
    (
        "trng",
        r"(?i)\bTRNG\b|\btrue\s+random|\bRNG\b|random\s+number\s+generator",
    ),
    (
        "ecc_crypto",
        r"(?i)\bECC\b(?=[^.\n]{0,40}(?:crypto|asymmetric|curve|ECDSA|ECDH|public))|\bECDSA\b|\bECDH\b|\bPKA\b|elliptic",
    ),
    ("rsa", r"\bRSA\b"),
    ("trustzone", r"(?i)TrustZone|\bTZ\b"),
    (
        "secure_boot",
        r"(?i)secure\s+boot|root\s+of\s+trust|\bRoT\b",
    ),
    ("tamper", r"(?i)\btamper"),
    (
        "unique_id",
        r"(?i)unique\s+(?:device\s+)?ID|\bUID\b|\bUUID\b",
    ),
    (
        "readout_protection",
        r"(?i)read-?out\s+protection|\bRDP\b|code\s+protection|flash\s+(?:area\s+)?protection|write\s+protection",
    ),
    ("mpu", r"(?i)\bMPU\b|memory\s+protection\s+unit"),
    (
        "memory_ecc",
        r"(?i)\bECC\b(?![^.\n]{0,40}(?:crypto|asymmetric|curve|ECDSA|ECDH|public))|error\s+correct",
    ),
    ("parity", r"(?i)\bparity\b"),
    ("crc", r"\bCRC(?:-?\d+)?\b"),
    (
        "watchdog",
        r"(?i)watchdog|\bWDT\b|\bIWDG\b|\bWWDG\b|\bIWDT\b",
    ),
    (
        "secure_debug",
        r"(?i)debug\s+(?:access\s+level|protection|authentication)|secure\s+debug",
    ),
    (
        "key_storage",
        r"(?i)key\s+(?:storage|injection|wrap|management)|\bHUK\b|\bPUF\b",
    ),
    (
        "functional_safety",
        r"(?i)IEC\s*61508|ISO\s*26262|\bSIL\s?\d|\bASIL|class\s+B|UL\s*60730",
    ),
    (
        "hardware_crypto_generic",
        r"(?i)cryptograph|\bcrypto\b|\bRSIP\b|\bSCE\d?\b|\bHASH\b|\bCRYP\b|\bSAES\b",
    ),
];

/// `_PROTOCOL_FLAGS` — connectivity protocol probes.
pub const PROTOCOL_FLAGS: &[(&str, &str)] = &[
    ("lin", r"\bLIN\b"),
    ("irda", r"\bIrDA\b"),
    ("smartcard", r"\bsmart\s?card\b|ISO\s?7816"),
    ("modbus", r"\bModbus\b"),
    ("ieee1588", r"IEEE\s?1588|\bPTP\b"),
    ("rs485", r"RS-?485"),
    (
        "i2c_fm_plus",
        r"fast[- ]mode\s+plus|\bFm\+|1\s*Mbit/s\s*(?:\(fast\s+mode\s+plus\))?",
    ),
    ("smbus", r"\bSMBus\b"),
    ("pmbus", r"\bPMBus\b"),
    ("i2s", r"\bI2S\b"),
    ("spdif", r"S/PDIF|\bSPDIF"),
    ("quad_spi", r"quad[- ]?SPI|\bQSPI\b|\bQUADSPI\b"),
    (
        "octo_spi",
        r"octo[- ]?SPI|\bOSPI\b|\bOCTOSPI\b|\bXSPI\b|hexadeca",
    ),
    ("usb_type_c", r"type[- ]C|\bUCPD\b|power\s+delivery"),
    ("hdmi_cec", r"HDMI[- ]CEC|\bCEC\b"),
    ("sdio", r"\bSDIO\b|\bSDMMC\b|\bSD/MMC\b|\bSDHC\b|\beMMC\b"),
    ("can_fd", r"CAN[- ]?FD|FDCAN|flexible\s+data"),
    ("tt_can", r"TT-?CAN|time[- ]triggered"),
];

// ---------------------------------------------------------------- patterns

/// `_RANGE`.
pub const RANGE: &str = r"\d+(?:\.\d+)?\s*(?:-|–|to)\s*\d+(?:\.\d+)?";
/// `_COMMA_LIST`.
pub const COMMA_LIST: &str = r"\b\d{1,3}(?:,\s+\d{1,3})+(?:,?\s+(?:and|or)\s+\d{1,3})?\b|\b\d{1,3}\s+(?:and|or)\s+\d{1,3}\s+(?:leads|pins|packages)\b";
/// `_ALT_LIST` (case-insensitive).
pub const ALT_LIST: &str = r"(?i)\b\d{1,4}\s*(?:or|/)\s*\d{1,4}(?:\s*/\s*\d{1,4})*\s*-?\s*(?:KB|MB|Kbytes?|Mbytes?|Kbit|Mbit|I/Os?|pins?|leads?|MHz|channels?)\b";
/// `_VARIES` (case-insensitive).
pub const VARIES: &str = r"(?i)depending on|varies|device[- ]dependent|according to the (?:device|part|package)|see (?:the )?datasheet";
/// `_SENTENCE_START` (case-insensitive except the scoped `Can` branch).
pub const SENTENCE_START: &str = r"(?i)^(?:the|this|these|it|for|refer|see|section|to|when|if|in|on|a|an|all|note|may|allows?|supports?|provides?|enables?|includes?|load|output|pin|gluelessly|devices?)\b|(?-i:^Can\s+[a-z])";
/// `_NOT_CAPACITY` (case-insensitive).
pub const NOT_CAPACITY: &str = r"(?i)^\s*note\b|^\s*\(|\bfirst\b|\barea of\b|\bunits? of\b|\bgranularity|\berase\b|\bprogram(?:ming)?\s*:|\bblank\s+check|\bpage\s+size|\bblock\s+size|\bsector\s+size|\bsectors?\s+of\b|\bword\b|\bper\s+(?:sector|page|block)|\bminimum\s+(?:erase|program)|\bphrase\b|\brecord\s+size|\bcache\s+line|\bline\s+size|\bboundar";
/// `_MODE_LEAF` (case-insensitive).
pub const MODE_LEAF: &str = r"(?i)^(?:simple|smart\s*card|manchester|asynchronous|synchronous|half[- ]duplex|full[- ]duplex|master|slave|mode|modes|supports?|support\s+for|peripherals?\s+supported)\b";
/// `_SUPPLY_RANGE_TEXT`.
pub const SUPPLY_RANGE_TEXT: &str = r"\b\d\.\d{1,2}\s*V?\s*(?:to|–|-|~)\s*\d\.\d{1,2}\s*V\b";
/// `_TEMP_RANGE_TEXT`.
pub const TEMP_RANGE_TEXT: &str =
    r"[-–−]\s*\d{1,3}\s*(?:°\s*C|ºC|℃)?\s*(?:to|~|–|-|…)\s*\+?\s*\d{1,3}\s*(?:°\s*C|ºC|℃)";
/// `_SIZE_IN_TEXT` (case-insensitive).
pub const SIZE_IN_TEXT: &str = r"(?i)\d\s*-?\s*(?:KB|MB|Kbytes?|Mbytes?|bytes|Kbit|Mbit)\b";
/// `_MODE_WORDS` alternation body (used case-insensitively).
pub const MODE_WORDS: &str = r"(?i)(run|sleep|low[- ]power\s+run|low[- ]power\s+sleep|lprun|lpsleep|stop\s*\d?|standby|shutdown|deep[- ]sleep|deep\s+power[- ]down|power[- ]down|vbat|backup|idle|snooze|hibernate|em\d|active|software\s+standby|halt|wait)";
/// `_CURRENT` (case-insensitive).
pub const CURRENT: &str = r"(?i)(\d+(?:\.\d+)?)\s*(nA|µA|uA|μA|mA)(?:/MHz)?\b";
/// `_CURRENT_PER_MHZ` (case-insensitive).
pub const CURRENT_PER_MHZ: &str = r"(?i)(\d+(?:\.\d+)?)\s*(µA|uA|μA)\s*/\s*MHz";
/// `_WAKEUP` (case-insensitive).
pub const WAKEUP: &str = r"(?i)wake[- ]?up[^.\n]{0,40}?(\d+(?:\.\d+)?)\s*(µs|us|μs|ns|ms)|(\d+(?:\.\d+)?)\s*(µs|us|μs|ns|ms)[^.\n]{0,20}wake";
/// `_BITS` (case-insensitive).
pub const BITS: &str = r"(?i)(\d{1,2})[- ]bit";
/// `_CHANNELS` (case-insensitive).
pub const CHANNELS: &str =
    r"(?i)(?:up\s+to\s+)?(\d{1,3})\s*(?:external\s+|input\s+|analog\s+)?(?:channels?|ch\b|inputs?)";
/// `_SAMPLE_RATE` (case-insensitive).
pub const SAMPLE_RATE: &str =
    r"(?i)(\d+(?:\.\d+)?)\s*(Msps|ksps|MSPS|kSPS|Ms/s|ks/s|MHz\s+sampling)";
/// `_BITRATE` (case-insensitive).
pub const BITRATE: &str = r"(?i)(\d+(?:\.\d+)?)\s*(Mbit/s|Mbps|kbit/s|kbps|Gbit/s|Mbyte/s|MB/s)";
/// `_USB_SPEED` (case-**sensitive** in the reference — kept so).
pub const USB_SPEED: &str =
    r"\b(full[- ]speed|high[- ]speed|low[- ]speed|super[- ]speed|\bFS\b|\bHS\b)";
/// `_LCD_SEG` (case-insensitive).
pub const LCD_SEG: &str = r"(?i)(\d{1,2})\s*[×x]\s*(\d{1,3})\s*segment";
/// `_TOUCH_CH` (case-insensitive).
pub const TOUCH_CH: &str = r"(?i)(\d{1,3})[- ]channel\s+(?:capacitive\s+)?touch|touch[^.\n]{0,30}?(\d{1,3})\s*(?:channels?|electrodes?|inputs?)";
/// `_RESOLUTION_PX` (case-insensitive).
pub const RESOLUTION_PX: &str = r"(?i)(\d{3,4})\s*[×x]\s*(\d{3,4})";
/// `_PWM_CH` (case-insensitive).
pub const PWM_CH: &str = r"(?i)(\d{1,3})\s*(?:PWM\s+)?(?:channels?|outputs?)[^.\n]{0,15}PWM|PWM[^.\n]{0,30}?(\d{1,3})\s*(?:channels?|outputs?)";
/// `_CORE_NAME` (case-insensitive).
pub const CORE_NAME: &str = r"(?i)\b(Cortex[®™]?-?\s?[MRA]\d{1,2}\+?F?|C8051|CIP-51|8051|RISC-V|C28x|RXv[123]|RL78|MIPS32|MIPS\s+M\d{1,2}[Kk]|microAptiv|AVR|PIC18|PIC16|TriCore|Xtensa|ARM7TDMI|ARM9|ARM926)\b";
/// `_CORE_MHZ` (case-insensitive).
pub const CORE_MHZ: &str = r"(?i)(?:up\s+to\s+|@\s*|at\s+|of\s+)?(\d{1,4}(?:\.\d+)?)\s*-?\s*MHz";
/// `_DMIPS` (case-insensitive; lookahead needs fancy-regex).
pub const DMIPS: &str = r"(?i)(\d{2,4}(?:\.\d+)?)\s*DMIPS(?!/MHz)|(\d(?:\.\d+)?)\s*DMIPS/MHz";
/// `_COREMARK` (case-insensitive).
pub const COREMARK: &str = r"(?i)(\d{2,4}(?:\.\d+)?)\s*CoreMark";
/// `_FPU` (case-insensitive).
pub const FPU: &str = r"(?i)\b(?:FPU|floating[- ]point(?:\s+unit)?)\b";
/// `_FPU_PRECISION` (case-insensitive).
pub const FPU_PRECISION: &str = r"(?i)\b(single|double)[- ]precision";
/// `_CORE_WIDTH` (case-insensitive).
pub const CORE_WIDTH: &str = r"(?i)\b(8|16|32|64)[- ]bit\b";
/// `_MULTI_CORE` (case-insensitive).
pub const MULTI_CORE: &str =
    r"(?i)\b(dual|two|2|quad|four|4|triple|three|3)[- ]?cores?\b|\b(dual|quad)[- ]core\b";
/// `_OSC` (case-insensitive; two branches, group 1 / group 2).
pub const OSC: &str = r"(?i)\b(HSI48|HSI|HSE|LSI|LSE|MSI|CSI|HSI16|IRC48M|IRC8M|IRC40K|IRC32K|LOCO|HOCO|MOCO|SOSC|MOSC|FRO|IRC|LPOSC|OSC32K|XOSC32K|XOSC|DFLL48M|DFLL|OSC8M|OSC16M|OSC48M|HFRCO|LFRCO|AUXHFRCO|HFXO|LFXO|ULFRCO|PLL\d?|SYSOSC|LFOSC|BLPE|ROSC|SIRC|FIRC|SOSC|MCG)\b|\b(internal|on-chip|external|low[- ]speed|high[- ]speed|low[- ]frequency|high[- ]frequency|low[- ]power)\s+(?:\w+\s+){0,2}?(?:RC\s+)?(?:oscillator|crystal|clock|reference\s+clock)\b";
/// `_OSC_FREQ` (case-insensitive).
pub const OSC_FREQ: &str = r"(?i)(\d+(?:\.\d+)?)\s*-?\s*(MHz|kHz|Hz)\b";
/// `_LP_MODE` (case-insensitive).
pub const LP_MODE: &str = r"(?i)\b(Sleep|Stop\s?\d?|Standby|Shutdown|Deep[- ]?Sleep|Deep\s+power[- ]down|Power[- ]down|Hibernate|Snooze|Idle|Halt|Wait|Backup|VBAT|Low[- ]power\s+run|Low[- ]power\s+sleep|LPRUN|LPSLEEP|EM[1-4]|VLLS\d?|LLS\d?|VLPS|VLPR|VLPW|Software\s+standby|Deep\s+software\s+standby)\b";
/// `_TIMER_CHANNELS` (case-insensitive).
pub const TIMER_CHANNELS: &str = r"(?i)(\d{1,2})(?:\s+or\s+(\d{1,2}))?[- ]channels?\b|(\d{1,2})(?:\s+or\s+(\d{1,2}))?\s+(?:compare/capture(?:/PWM)?|capture/compare(?:/PWM)?|input\s+capture|output\s+compare|IC/OC(?:/PWM)?|PWM)\s+channels?";
/// `_TIMER_MHZ` (case-insensitive).
pub const TIMER_MHZ: &str = r"(?i)(?:up\s+to\s+|@\s*|at\s+)?(\d{1,4})\s*MHz";
/// `_VREF` (case-insensitive).
pub const VREF: &str = r"(?i)(?:reference\s+voltage|voltage\s+reference|VREF(?:INT|BUF)?)[^.\n]{0,25}?\(?\s*(\d(?:\.\d+)?)\s*V\b|(\d(?:\.\d+)?)\s*V\s+(?:internal\s+)?(?:reference|VREF)";
/// `_CONVERSION_TIME` (case-insensitive).
pub const CONVERSION_TIME: &str = r"(?i)(\d+(?:\.\d+)?)\s*(µs|us|μs|ns)\s*(?:conversion|conv\.)|conversion(?:\s+time)?[^.\n]{0,20}?(\d+(?:\.\d+)?)\s*(µs|us|μs|ns)";
/// `_NON_SCOPE` (case-insensitive, `match`-anchored at 0 in the reference).
pub const NON_SCOPE: &str = r"(?i)^(?:\d{1,2}-?bit\s+)?(?:mcu|mcus|bit mcu|microcontrollers?|group|series|family|arm|based)$";

/// `_QQ_CODE_FLASH`.
pub const QQ_CODE_FLASH: &str = r"(?i)\bcode\s+flash|\bprogram\s+(?:flash|memory)|\bflash\s+program\s+memory|\bapplication\s+(?:flash|code)|\bmain\s+flash|\bembedded\s+flash\s+memory\s+\(.*code|\binstruction\s+flash|\bcode\s+memory|\bcode\s+storage|\bprogram\s+space";
/// `_QQ_DATA_FLASH` (negative lookahead needs fancy-regex).
pub const QQ_DATA_FLASH: &str =
    r"(?i)\bdata\s+flash|\bdata\s+memory\b(?!\s*\(sram)|\bemulated\s+eeprom|\bee\s+memory";
/// `_QQ_EEPROM`.
pub const QQ_EEPROM: &str = r"(?i)\beeprom\b";
/// `_QQ_SRAM_BANK`.
pub const QQ_SRAM_BANK: &str = r"(?i)\bsram\s?\d\b|\bsram[A-D]\b|\bsram\s+bank|\bbank\s*\d|\bitcm|\bdtcm|\btcm\b|\bccm\b|\baxi\s+sram|\bahb\s+sram|\bmain\s+internal\s+sram|\bauxiliary\s+internal\s+sram|\bd\d\s+domain";
/// `_QQ_SRAM_SPECIAL`.
pub const QQ_SRAM_SPECIAL: &str = r"(?i)\bbackup\b|\bretention\b|\bcache\b|\bmessage\s+ram|\bdma\s+ram|\bdpsram|\busb\s+ram|\bstandby\s+sram|\bparity\s+ram|\becc\s+ram|\bflexram|\bocram|\bperipheral\s+ram|\bmailbox";
/// `_QQ_AMBIENT`.
pub const QQ_AMBIENT: &str = r"(?i)\bTa\b|\bT\s*A\b|ambient";
/// `_QQ_JUNCTION`.
pub const QQ_JUNCTION: &str = r"(?i)\bTj\b|\bT\s*J\b|junction";
/// `_QQ_STORAGE`.
pub const QQ_STORAGE: &str = r"(?i)\bstorage\b|\bTstg\b";
/// `_QQ_ABS_MAX`.
pub const QQ_ABS_MAX: &str = r"(?i)absolute\s+max|\babs\.?\s*max|\bmaximum\s+ratings?";
/// `_QQ_RATED`.
pub const QQ_RATED: &str = r"(?i)operating\s+(?:voltage|range|conditions?)|supply\s+voltage|power\s+supply|\bV(?:DD|CC)\b|recommended|\bsupply\b|\boperating\b|\boperation\b|\bvoltage\s+range\b";
/// `_QQ_TYPICAL`.
pub const QQ_TYPICAL: &str = r"(?i)\btyp(?:ical|\.)?\b";
/// `_QQ_MINIMUM`.
pub const QQ_MINIMUM: &str = r"(?i)\bmin(?:imum|\.)?\b";
/// `_QQ_MAXIMUM`.
pub const QQ_MAXIMUM: &str = r"(?i)\bmax(?:imum|\.)?\b|\bup\s+to\b";

/// `_vendor_stem` stripping pattern (lookbehind needs fancy-regex).
pub const STEM_STRIP: &str = r"(?<=[A-Za-z])[0-9]{1,2}$|(?<=GPIO)[A-Z]$|(?<=GIO)[A-H]$|(?<=SCI)[A-D]$|(?<=ADC)[A-D]$|(?<=DAC)[A-D]$";
/// `_label_key` acronym-plural pattern (case-**sensitive**).
pub const ACRONYM_PLURAL: &str = r"\b([A-Z][A-Z0-9]{1,7})s\b";
/// `_power_fact` probe (case-insensitive; lookbehind needs fancy-regex).
pub const POWER_FACT: &str = r"(?i)\b(?:V|mV|µA|μA|uA|mA|nA)\b|(?<=\d\s)(?:µA|μA)|/MHz|supply|standby|wake-?up|\bPLL\b|reset|LVD|BOR|POR|\bcurrent\b|\bEM[0-4]\b|energy mode|dc-?dc|\bLDO\b|regulator";
/// `_package_or_temp` probe.
pub const PACKAGE_OR_TEMP: &str = r"\b(?:LQFP|QFN|BGA|CSP|TSSOP|SOIC|DIP|WLCSP|UFBGA|TFBGA|packages?|pins?|leads?|°C|ºC|I/Os?|GPIOs?)\b";
/// Chapter-title numeral prefix (`chapter_titles` construction).
pub const TITLE_NUMERAL: &str = r"^\s*\d{1,2}(?:\.\d+)*\s+";
