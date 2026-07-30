// Dump decompiled C for functions touching ADL i2c and resource-loading APIs.
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.Symbol;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.DataIterator;
import java.util.*;

public class DumpADL extends GhidraScript {

    private static final String[] NEEDLES = {
        "WriteAndReadI2C", "DDCBlockAccess", "FindResource", "LockResource",
        "SizeofResource", "I2CFailedException", "CryptDecrypt", "BCryptDecrypt",
        "Aes", "atiadlxx", "ADL2_Main_Control_Create",
    };

    private DecompInterface decomp;

    public void run() throws Exception {
        decomp = new DecompInterface();
        decomp.openProgram(currentProgram);

        Set<Function> seeds = new LinkedHashSet<>();
        Map<Function, String> why = new HashMap<>();

        // 1. functions that reference a matching defined string
        DataIterator dit = currentProgram.getListing().getDefinedData(true);
        while (dit.hasNext()) {
            Data d = dit.next();
            if (!d.hasStringValue()) continue;
            Object ov = d.getValue();
            String v = (ov == null) ? null : ov.toString();
            if (v == null) continue;
            String hit = match(v);
            if (hit == null) continue;
            for (Reference r : getReferencesTo(d.getAddress())) {
                Function f = getFunctionContaining(r.getFromAddress());
                if (f != null && seeds.add(f)) why.put(f, "string \"" + v + "\"");
            }
        }

        // 2. functions that call a matching symbol (imports / thunks)
        for (Symbol sym : currentProgram.getSymbolTable().getAllSymbols(true)) {
            String hit = match(sym.getName());
            if (hit == null) continue;
            for (Reference r : getReferencesTo(sym.getAddress())) {
                Function f = getFunctionContaining(r.getFromAddress());
                if (f != null && seeds.add(f)) why.put(f, "calls " + sym.getName());
            }
        }

        println("=== SEEDS: " + seeds.size() + " ===");
        for (Function f : seeds) println("SEED " + f.getEntryPoint() + " " + f.getName() + "  [" + why.get(f) + "]");

        // include one level of callers so we see the arguments passed in
        Set<Function> all = new LinkedHashSet<>(seeds);
        for (Function f : seeds) {
            for (Function c : f.getCallingFunctions(monitor)) all.add(c);
        }

        println("=== DECOMPILING " + all.size() + " FUNCTIONS ===");
        for (Function f : all) {
            println("");
            println("//======================================================");
            println("// " + f.getEntryPoint() + "  " + f.getName()
                    + (seeds.contains(f) ? "   [SEED: " + why.get(f) + "]" : "   [caller]"));
            println("//======================================================");
            DecompileResults res = decomp.decompileFunction(f, 90, monitor);
            if (res.decompileCompleted() && res.getDecompiledFunction() != null) {
                println(res.getDecompiledFunction().getC());
            } else {
                println("// decompile failed: " + res.getErrorMessage());
            }
        }
        decomp.dispose();
    }

    private String match(String s) {
        for (String n : NEEDLES) if (s.contains(n)) return n;
        return null;
    }
}
