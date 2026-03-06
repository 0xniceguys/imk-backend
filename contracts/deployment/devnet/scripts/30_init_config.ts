// @ts-nocheck
import * as anchor from "@coral-xyz/anchor";
import { PublicKey, SystemProgram } from "@solana/web3.js";
import { TOKEN_PROGRAM_ID } from "@solana/spl-token";
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

const skrMint = new PublicKey(env.SKR_MINT);
const treasuryWallet = new PublicKey(env.TREASURY_WALLET);
const minBet = new anchor.BN(env.MIN_BET_BASE_UNITS);
const maxBet = new anchor.BN(env.MAX_BET_BASE_UNITS);

const [configPda] = PublicKey.findProgramAddressSync([Buffer.from("config")], program.programId);

async function main() {
  const sig = await program.methods
    .initConfig(minBet, maxBet)
    .accountsStrict({
      config: configPda,
      admin: provider.wallet.publicKey,
      skrMint,
      treasuryWallet,
      tokenProgram: TOKEN_PROGRAM_ID,
      systemProgram: SystemProgram.programId,
    })
    .rpc();

  console.log("init_config signature:", sig);
  console.log("config PDA:", configPda.toBase58());
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
