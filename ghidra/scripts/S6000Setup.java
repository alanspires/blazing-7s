// Sets up the Bally ProSlot 6000 MPU memory map, vector table and DUART register labels
// for the merged main-program image (U28 even bytes, U43 odd bytes) loaded at 0x000000.
// Safe to rerun on an already set-up program.
// Map source: MK14-S6MOD-0001 schematics (MAINS 000000-0FFFFF, PERSONALITY 100000-11FFFF,
// SCRATCH RAM 400000-437FFF, SAFERAM 438000-43FFFF, MC68681 DUARTs at FE1000/FE1020/FE1060).
//@category Bally

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.data.DWordDataType;
import ghidra.program.model.data.PointerDataType;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.SourceType;
import ghidra.program.model.symbol.Symbol;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.nio.file.Files;

public class S6000Setup extends GhidraScript {

    private static final String PERSONALITY_ROM = "personality_E595211X-05.bin";

    private static final String[] DUART_REGS = {
        "MR_A", "SR_CSR_A", "CR_A", "RHR_THR_A", "IPCR_ACR", "ISR_IMR", "CTU_CTUR", "CTL_CTLR",
        "MR_B", "SR_CSR_B", "CR_B", "RHR_THR_B", "IVR", "IP_OPCR", "STARTCC_SETOP", "STOPCC_RESETOP"
    };

    @Override
    protected void run() throws Exception {
        Memory mem = currentProgram.getMemory();
        MemoryBlock rom = mem.getBlock(toAddr(0));
        rom.setName("ROM_MAIN");
        rom.setWrite(false);
        rom.setExecute(true);

        addPersonality(mem);
        addBlock(mem, "RAM_SCRATCH", 0x400000L, 0x38000L, false);
        addBlock(mem, "RAM_SAFERAM", 0x438000L, 0x8000L, false);
        addBlock(mem, "IO_FE", 0xFE0000L, 0x10000L, true);
        addBlock(mem, "IO_FF", 0xFF0000L, 0x10000L, true);

        String[] names = new String[256];
        names[0] = "VEC_SSP";
        names[1] = "VEC_RESET";
        names[2] = "VEC_BUS_ERROR";
        names[3] = "VEC_ADDRESS_ERROR";
        names[4] = "VEC_ILLEGAL";
        names[5] = "VEC_ZERO_DIVIDE";
        names[6] = "VEC_CHK";
        names[7] = "VEC_TRAPV";
        names[8] = "VEC_PRIVILEGE";
        names[9] = "VEC_TRACE";
        names[10] = "VEC_LINE_A";
        names[11] = "VEC_LINE_F";
        names[24] = "VEC_SPURIOUS";
        for (int i = 1; i <= 7; i++) {
            names[24 + i] = "VEC_AUTOVECTOR_" + i;
        }
        for (int i = 0; i <= 15; i++) {
            names[32 + i] = "VEC_TRAP_" + i;
        }
        names[0x40] = "VEC_USER_40";
        names[0x41] = "VEC_DUART1_FE1000";
        names[0x42] = "VEC_DUART2_FE1020";
        names[0x43] = "VEC_DUART3_FE1060";

        for (int i = 0; i < 256; i++) {
            Address slot = toAddr(i * 4L);
            String name = names[i] != null ? names[i] : String.format("VEC_%02X", i);
            label(slot, name);
            if (getDataAt(slot) == null) {
                createData(slot, i == 0 ? DWordDataType.dataType : PointerDataType.dataType);
            }
            if (i == 0) {
                continue;
            }
            long target = mem.getInt(slot) & 0xFFFFFFFFL;
            if (names[i] != null && target < rom.getSize()) {
                Address entry = toAddr(target);
                if (getFunctionAt(entry) == null) {
                    disassemble(entry);
                    createFunction(entry, i == 1 ? "reset_entry" : name.replaceFirst("^VEC_", "isr_").toLowerCase());
                }
                addEntryPoint(entry);
            }
        }

        labelDuart(0xFE1000L, "DUART1");
        labelDuart(0xFE1020L, "DUART2");
        labelDuart(0xFE1060L, "DUART3");
    }

    // The game (personality) EPROM pair U20 (even) + U18 (odd); its part number string sits at
    // 0x10D1C0 and is reported to bonus devices by the J3 status frame.
    private void addPersonality(Memory mem) throws Exception {
        if (mem.getBlock(toAddr(0x100000L)) != null) {
            return;
        }
        File repoRoot = getSourceFile().getParentFile().getParentFile().getParentFile().getFile(false);
        File image = new File(new File(repoRoot, "roms"), PERSONALITY_ROM);
        byte[] data = Files.readAllBytes(image.toPath());
        MemoryBlock block = mem.createInitializedBlock("ROM_PERSONALITY", toAddr(0x100000L),
            new ByteArrayInputStream(data), data.length, monitor, false);
        block.setWrite(false);
        block.setComment(PERSONALITY_ROM);
        label(toAddr(0x10D1C0L), "PERSONALITY_PART_NUMBER");
    }

    private void addBlock(Memory mem, String name, long start, long size, boolean io) throws Exception {
        MemoryBlock existing = mem.getBlock(toAddr(start));
        if (existing != null && existing.getStart().getOffset() == start && existing.getSize() > size) {
            mem.split(existing, toAddr(start + size));
        }
        existing = mem.getBlock(toAddr(start));
        if (existing != null) {
            existing.setName(name);
            return;
        }
        MemoryBlock block = mem.createUninitializedBlock(name, toAddr(start), size, false);
        block.setRead(true);
        block.setWrite(true);
        block.setExecute(false);
        block.setVolatile(io);
    }

    private void label(Address at, String name) throws Exception {
        for (Symbol s : currentProgram.getSymbolTable().getSymbols(at)) {
            if (s.getName().equals(name)) {
                return;
            }
        }
        createLabel(at, name, true, SourceType.USER_DEFINED);
    }

    // MC68681 register select RS1..RS4 is wired to A1..A4 and the data bus to D0..D7, so
    // register n sits at base + 2n + 1 (the odd byte lane).
    private void labelDuart(long base, String prefix) throws Exception {
        for (int r = 0; r < DUART_REGS.length; r++) {
            label(toAddr(base + 2L * r + 1), prefix + "_" + DUART_REGS[r]);
        }
    }
}
