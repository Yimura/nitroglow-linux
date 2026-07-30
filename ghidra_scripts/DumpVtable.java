// Find vtable slots holding the i2c primitives, then dump functions that reference
// those vtables (constructors) so we can see where iLine (+0x18) is initialised.
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;
import java.util.*;

public class DumpVtable extends GhidraScript {

    private static final String[] SEEDS = { "00431fa0", "004320e0", "00431e80", "0042b980" };
    private static final int LOOKBACK_SLOTS = 60;

    public void run() throws Exception {
        DecompInterface decomp = new DecompInterface();
        decomp.openProgram(currentProgram);

        Set<Address> vtableSlots = new LinkedHashSet<>();

        for (String s : SEEDS) {
            Address a = currentProgram.getAddressFactory().getAddress(s);
            println("=== refs to " + s + " ===");
            ReferenceIterator it = currentProgram.getReferenceManager().getReferencesTo(a);
            while (it.hasNext()) {
                Reference r = it.next();
                Address from = r.getFromAddress();
                Function inFn = getFunctionContaining(from);
                println("  from " + from + " type=" + r.getReferenceType()
                        + " block=" + blockName(from)
                        + (inFn != null ? " inFunc=" + inFn.getName() : " (data)"));
                if (inFn == null) vtableSlots.add(from);
            }
        }

        // For each vtable slot, walk backwards slot by slot looking for anything that
        // references that address - the vtable base is what constructors load.
        Set<Function> ctors = new LinkedHashSet<>();
        for (Address slot : vtableSlots) {
            println("=== walking back from vtable slot " + slot + " ===");
            for (int k = 0; k <= LOOKBACK_SLOTS; k++) {
                Address cand = slot.subtract(4L * k);
                ReferenceIterator it = currentProgram.getReferenceManager().getReferencesTo(cand);
                while (it.hasNext()) {
                    Reference r = it.next();
                    Function f = getFunctionContaining(r.getFromAddress());
                    if (f != null) {
                        println("  vtable base candidate " + cand + " (slot -" + k + ") referenced by "
                                + f.getName() + " @ " + r.getFromAddress());
                        ctors.add(f);
                    }
                }
            }
        }

        println("=== DECOMPILING " + ctors.size() + " REFERENCING FUNCTIONS ===");
        for (Function f : ctors) {
            println("");
            println("//###### " + f.getEntryPoint() + "  " + f.getName());
            DecompileResults res = decomp.decompileFunction(f, 90, monitor);
            if (res.decompileCompleted() && res.getDecompiledFunction() != null) {
                println(res.getDecompiledFunction().getC());
            } else {
                println("// decompile failed: " + res.getErrorMessage());
            }
        }
        decomp.dispose();
    }

    private String blockName(Address a) {
        var b = currentProgram.getMemory().getBlock(a);
        return b == null ? "?" : b.getName();
    }
}
