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
if (!programIdStr) throw new Error("BETTING_PROGRAM_ID missing");

const expectedMint = env.SKR_MINT;
const expectedTreasury = env.TREASURY_WALLET;

const program = new anchor.Program(idl, new PublicKey(programIdStr), provider);
const [configPda] = PublicKey.findProgramAddressSync([Buffer.from("config")], program.programId);

async function checkProgramAccount() {
  const info = await provider.connection.getAccountInfo(program.programId);
  if (!info) throw new Error(`Program account not found: ${program.programId.toBase58()}`);
  if (!info.executable) throw new Error("Program account is not executable");
}

async function checkConfigAccount() {
  const cfg = await program.account.config.fetch(configPda);

  const failures: string[] = [];
  if (expectedMint && cfg.skrMint.toBase58() !== expectedMint) {
    failures.push(`skr_mint mismatch: on-chain=${cfg.skrMint.toBase58()} expected=${expectedMint}`);
  }
  if (expectedTreasury && cfg.treasuryWallet.toBase58() !== expectedTreasury) {
    failures.push(`treasury_wallet mismatch: on-chain=${cfg.treasuryWallet.toBase58()} expected=${expectedTreasury}`);
  }
  if (cfg.minBet.gt(cfg.maxBet)) {
    failures.push(`invalid range: min_bet ${cfg.minBet.toString()} > max_bet ${cfg.maxBet.toString()}`);
  }

  if (failures.length > 0) {
    throw new Error(failures.join("\n"));
  }

  console.log("[pass] config PDA:", configPda.toBase58());
  console.log("[pass] admin:", cfg.admin.toBase58());
  console.log("[pass] skr_mint:", cfg.skrMint.toBase58());
  console.log("[pass] treasury_wallet:", cfg.treasuryWallet.toBase58());
  console.log("[pass] fee_bps:", cfg.feeBps);
  console.log("[pass] min_bet:", cfg.minBet.toString());
  console.log("[pass] max_bet:", cfg.maxBet.toString());
  console.log("[pass] match_counter:", cfg.matchCounter.toString());
  console.log("[pass] paused:", cfg.paused);
}

async function main() {
  await checkProgramAccount();
  await checkConfigAccount();
  console.log("[done] devnet readonly smoke checks passed");
}

main().catch((err) => {
  console.error("[fail]", err.message || err);
  process.exit(1);
});
