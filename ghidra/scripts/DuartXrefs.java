// Lists every instruction that touches a DUART register (directly or through a base
// address loaded into an address register) and the functions they sit in.
//@category Bally

import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.scalar.Scalar;
import ghidra.program.model.symbol.Reference;

import java.util.Map;
import java.util.TreeMap;
import java.util.TreeSet;

public class DuartXrefs extends GhidraScript {

    @Override
    protected void run() throws Exception {
        long lo = 0xFE1000L;
        long hi = 0xFE10FFL;
        Map<String, TreeSet<String>> byFunction = new TreeMap<>();
        InstructionIterator it = currentProgram.getListing().getInstructions(true);
        while (it.hasNext()) {
            Instruction ins = it.next();
            boolean hit = false;
            for (Reference ref : ins.getReferencesFrom()) {
                long a = ref.getToAddress().getOffset();
                if (ref.getToAddress().isMemoryAddress() && a >= lo && a <= hi) {
                    hit = true;
                }
            }
            for (int op = 0; op < ins.getNumOperands() && !hit; op++) {
                for (Object o : ins.getOpObjects(op)) {
                    if (o instanceof Scalar s) {
                        long v = s.getUnsignedValue();
                        if (v >= lo && v <= hi) {
                            hit = true;
                        }
                    }
                }
            }
            if (!hit) {
                continue;
            }
            Function f = getFunctionContaining(ins.getAddress());
            String fn = f == null ? "<no function>" : f.getName() + "@" + f.getEntryPoint();
            byFunction.computeIfAbsent(fn, k -> new TreeSet<>()).add(ins.getAddress() + "  " + ins);
        }
        for (Map.Entry<String, TreeSet<String>> e : byFunction.entrySet()) {
            println("== " + e.getKey());
            for (String line : e.getValue()) {
                println("   " + line);
            }
        }
    }
}
