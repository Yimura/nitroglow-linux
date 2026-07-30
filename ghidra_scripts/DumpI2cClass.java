// 1. dump the i2c vtable at 0x53471c
// 2. find every function that stores to [reg+0x18] AND reads [reg+1] (managed struct -> native obj)
// 3. dump functions referencing the I2CFailedException RTTI / that call vtable slots of 0x53471c
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.scalar.Scalar;
import ghidra.program.model.symbol.Reference;
import java.util.*;

public class DumpI2cClass extends GhidraScript {
    DecompInterface decomp;

    void dump(String tag, Function f) {
        println("\n\n########## " + tag + " " + f.getEntryPoint() + " " + f.getName());
        try {
            DecompileResults r = decomp.decompileFunction(f, 120, monitor);
            if (r != null && r.getDecompiledFunction() != null) println(r.getDecompiledFunction().getC());
            else println("  [decompile failed]");
        } catch (Exception e) { println("  [ex " + e + "]"); }
    }

    public void run() throws Exception {
        decomp = new DecompInterface();
        decomp.openProgram(currentProgram);
        Memory mem = currentProgram.getMemory();

        println("=== VTABLE at 0x53471c ===");
        for (int i = 0; i < 12; i++) {
            Address a = currentProgram.getAddressFactory().getAddress(Long.toHexString(0x53471cL + i * 4));
            try {
                long v = mem.getInt(a) & 0xffffffffL;
                Function f = getFunctionAt(currentProgram.getAddressFactory().getAddress(Long.toHexString(v)));
                println(String.format("  slot %2d (+0x%02x) = 0x%08x  %s", i, i * 4, v, f == null ? "?" : f.getName()));
            } catch (Exception e) { println("  slot " + i + " unreadable"); }
        }

        // pattern scan: functions with a write to [reg+0x18] and a read of [reg+1]
        println("\n=== SCAN: funcs writing [x+0x18] and reading [y+0x1] ===");
        Map<Function, boolean[]> m = new HashMap<>();
        InstructionIterator it = currentProgram.getListing().getInstructions(true);
        while (it.hasNext()) {
            Instruction ins = it.next();
            Function f = getFunctionContaining(ins.getAddress());
            if (f == null) continue;
            String s = ins.toString();
            boolean[] b = m.computeIfAbsent(f, k -> new boolean[3]);
            // crude: textual match on displacement in a memory operand
            if (s.contains("+ 0x18]")) b[0] = true;
            if (s.contains("+ 0x1]")) b[1] = true;
            if (s.contains("+ 0xc]") || s.contains("+ 0x4]") || s.contains("+ 0x8]")) b[2] = true;
        }
        List<Function> hits = new ArrayList<>();
        for (Map.Entry<Function, boolean[]> e : m.entrySet())
            if (e.getValue()[0] && e.getValue()[1]) hits.add(e.getKey());
        println("  hits: " + hits.size());
        hits.sort(Comparator.comparing(Function::getEntryPoint));
        for (Function f : hits) println("    " + f.getEntryPoint() + " " + f.getName());
        for (Function f : hits) dump("STRUCTCOPY", f);
    }
}
