// Find the CLR-host bootstrap (refs to L"GUI.App" / L"Init") and the {id, fn} API table builder.
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;
import java.util.*;

public class FindApiTable extends GhidraScript {
    DecompInterface decomp;

    void dump(String tag, Function f) {
        println("\n\n########## " + tag + " " + f.getEntryPoint() + " " + f.getName());
        try {
            DecompileResults r = decomp.decompileFunction(f, 180, monitor);
            if (r != null && r.getDecompiledFunction() != null) println(r.getDecompiledFunction().getC());
            else println("  [decompile failed]");
        } catch (Exception e) { println("  [ex " + e + "]"); }
    }

    public void run() throws Exception {
        decomp = new DecompInterface();
        decomp.openProgram(currentProgram);
        String[] targets = {"5b5580", "5b558c", "5b53d0", "5b5830"};
        LinkedHashSet<Function> fs = new LinkedHashSet<>();
        for (String t : targets) {
            Address a = currentProgram.getAddressFactory().getAddress(t);
            for (Reference r : getReferencesTo(a)) {
                Function f = getFunctionContaining(r.getFromAddress());
                println("ref " + a + " <- " + r.getFromAddress() + " in " + (f == null ? "NONE" : f.getName()));
                if (f != null) fs.add(f);
            }
        }
        LinkedHashSet<Function> all = new LinkedHashSet<>(fs);
        for (Function f : fs) {
            for (Function c : f.getCallingFunctions(monitor)) all.add(c);
            for (Function c : f.getCalledFunctions(monitor)) if (!c.isThunk() && !c.isExternal()) all.add(c);
        }
        println("=== " + all.size() + " functions ===");
        for (Function f : all) dump("BOOT", f);
    }
}
