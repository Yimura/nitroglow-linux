// Locate the object that carries iLine at +0x18:
//  (a) all references to the i2c dispatch table at 0x53471c  -> ctor / installer
//  (b) callers (2 levels up) of the three ADL i2c wrappers
//  (c) functions whose instructions contain the Glow register constants
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.*;
import ghidra.program.model.scalar.Scalar;
import ghidra.program.model.symbol.Reference;
import java.util.*;

public class FindILine extends GhidraScript {
    DecompInterface decomp;

    void dump(String tag, Function f) {
        println("\n\n########## " + tag + " " + f.getEntryPoint() + " " + f.getName());
        if (f.isThunk() || f.isExternal()) { println("  [thunk/external]"); return; }
        try {
            DecompileResults r = decomp.decompileFunction(f, 90, monitor);
            if (r != null && r.getDecompiledFunction() != null) println(r.getDecompiledFunction().getC());
            else println("  [decompile failed]");
        } catch (Exception e) { println("  [ex " + e + "]"); }
    }

    public void run() throws Exception {
        decomp = new DecompInterface();
        decomp.openProgram(currentProgram);
        Listing lst = currentProgram.getListing();

        // ---- (a) refs into the dispatch table region 0x53471c .. +0x28
        println("=== SECTION A: refs to dispatch table 0x53471c ===");
        LinkedHashSet<Function> tableRefs = new LinkedHashSet<>();
        for (long off = 0; off < 0x30; off += 4) {
            Address a = currentProgram.getAddressFactory().getAddress(Long.toHexString(0x53471cL + off));
            for (Reference r : getReferencesTo(a)) {
                Function f = getFunctionContaining(r.getFromAddress());
                println("  ref " + a + " <- " + r.getFromAddress() + " (" + r.getReferenceType() + ") in "
                        + (f == null ? "NONE" : f.getName() + "@" + f.getEntryPoint()));
                if (f != null) tableRefs.add(f);
            }
        }
        for (Function f : tableRefs) dump("TABLEREF", f);
        // one level of callers of those
        LinkedHashSet<Function> up = new LinkedHashSet<>();
        for (Function f : tableRefs) for (Function c : f.getCallingFunctions(monitor)) if (!tableRefs.contains(c)) up.add(c);
        for (Function f : up) dump("TABLEREF-CALLER", f);

        // ---- (b) callers of the ADL i2c wrappers, 2 levels
        println("\n\n=== SECTION B: callers of ADL i2c wrappers ===");
        String[] seedAddrs = {"00431fa0", "004320e0", "00431e80", "0042b980"};
        LinkedHashSet<Function> seeds = new LinkedHashSet<>();
        for (String s : seedAddrs) {
            Function f = getFunctionAt(currentProgram.getAddressFactory().getAddress(s));
            if (f != null) seeds.add(f);
        }
        LinkedHashSet<Function> lvl = new LinkedHashSet<>(seeds), seen = new LinkedHashSet<>(seeds);
        for (int d = 0; d < 2; d++) {
            LinkedHashSet<Function> next = new LinkedHashSet<>();
            for (Function f : lvl) for (Function c : f.getCallingFunctions(monitor)) if (seen.add(c)) next.add(c);
            println("  level " + (d + 1) + " callers: " + next.size());
            for (Function f : next) println("    " + f.getEntryPoint() + " " + f.getName());
            for (Function f : next) dump("CALLER-L" + (d + 1), f);
            lvl = next;
        }

        // ---- (c) constant scan: functions containing Glow-ish immediates
        println("\n\n=== SECTION C: functions containing Glow register constants ===");
        // want funcs with 0x3e AND (0x1a or 0x1b or 0x1c) AND 0x28  -- or 0x50 (0x28<<1)
        Map<Function, Set<Long>> hits = new HashMap<>();
        long[] want = {0x28, 0x50, 0x51, 0x3e, 0x1a, 0x1b, 0x1c, 0x10};
        InstructionIterator it = lst.getInstructions(true);
        while (it.hasNext()) {
            Instruction ins = it.next();
            Function f = getFunctionContaining(ins.getAddress());
            if (f == null) continue;
            for (int i = 0; i < ins.getNumOperands(); i++) {
                for (Object o : ins.getOpObjects(i)) {
                    if (!(o instanceof Scalar)) continue;
                    long v = ((Scalar) o).getUnsignedValue();
                    for (long w : want) if (v == w) hits.computeIfAbsent(f, k -> new HashSet<>()).add(v);
                }
            }
        }
        List<Function> cands = new ArrayList<>();
        for (Map.Entry<Function, Set<Long>> e : hits.entrySet()) {
            Set<Long> s = e.getValue();
            boolean glowRegs = s.contains(0x3eL) && (s.contains(0x1aL) || s.contains(0x1bL) || s.contains(0x1cL));
            boolean addr = s.contains(0x28L) || s.contains(0x50L) || s.contains(0x51L);
            if (glowRegs && addr) cands.add(e.getKey());
        }
        println("  candidates: " + cands.size());
        for (Function f : cands) println("    " + f.getEntryPoint() + " " + f.getName() + " consts=" + hits.get(f));
        for (Function f : cands) dump("GLOWCAND", f);
    }
}
