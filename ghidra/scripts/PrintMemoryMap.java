// Prints the program's memory blocks.
//@category Bally

import ghidra.app.script.GhidraScript;
import ghidra.program.model.mem.MemoryBlock;

public class PrintMemoryMap extends GhidraScript {
    @Override
    protected void run() throws Exception {
        for (MemoryBlock b : currentProgram.getMemory().getBlocks()) {
            println(String.format("%-16s %s-%s %s%s%s%s", b.getName(), b.getStart(), b.getEnd(),
                b.isRead() ? "r" : "-", b.isWrite() ? "w" : "-", b.isExecute() ? "x" : "-",
                b.isVolatile() ? " volatile" : ""));
        }
    }
}
