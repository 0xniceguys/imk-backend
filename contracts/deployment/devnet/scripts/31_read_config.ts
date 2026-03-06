// @ts-nocheck
import * as anchor from "@coral-xyz/anchor";
import { PublicKey } from "@solana/web3.js";
import { readFileSync } from "fs";
import { join } from "path";

const env = process.env;
const idlPath = env.IDL_PATH || join(process.cwd(), "target/idl/skr_parimutuel_betting.json");
const idl = JSON.parse(readFileSync(idlPath, "utf-8"));

const provider = anchor.AnchorProvider.env();
anchor.setProvider(provider);

const programIdStr = env.BETTING_PROGRAM_ID || idl.address || idl?.metadata?.address;
if (!programIdStr) {
  throw new Error("BETTING_PROGRAM_ID is required (or present in IDL address)");
}

const program = new anchor.Program(idl, new PublicKey(programIdStr), provider);
const [configPda] = PublicKey.findProgramAddressSync([Buffer.from("config")], program.programId);

function normalize(v: any): any {
  if (v && typeof v.toString === "function" && v.constructor?.name === "BN") {
    return v.toString();
  }
  if (Array.isArray(v)) return v.map(normalize);
  if (v && typeof v === "object") {
    const out: any = {};
    for (const [k, val] of Object.entries(v)) out[k] = normalize(val);
    return out;
  }
  return v;
}

async function main() {
  const cfg = await program.account.config.fetch(configPda);
  console.log(JSON.stringify({ configPda: configPda.toBase58(), config: normalize(cfg) }, null, 2));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
